"""Canary: does a real YouTube download start, right now, from this pod?

Until now every YouTube breakage reached us as a user noticing a failed
download, then got diagnosed by hand with a handful of noisy runs (see
docs/incidents/2026-11-21-pinned-player-client.md). This probes on a schedule
instead, through the *same* options real downloads use, and records:

- whether the stream actually delivered data (the 2026-11-17 403s hit the
  first stream request, after metadata and the PO Token both succeeded --
  a metadata-only check would have passed),
- the error kind when it didn't (services/ytdlp_errors.py),
- per cookies owner: whether the file is a login, and whether it still is
  after talking to YouTube (rotation shows up as LOGIN_INFO disappearing).

Each probe downloads into a throwaway directory and stops after
CANARY_PROBE_BYTES of the first stream. Owner cookies are copied first, so
the canary never rewrites a user's file (yt-dlp saves the jar back on exit).

Results live in memory: they are only ever "the latest", and the gauges and
the API both read them from here. CANARY_INTERVAL_MINUTES=0 disables it.
"""

import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from logging import getLogger
from os import environ
from pathlib import Path

import yt_dlp

from services import cookies_health
from services.telemetry import create_cached_gauge, meter
from services.ytdlp_errors import ErrorKind, classify
from services.ytdlp_options import youtube_opts

logger = getLogger(__name__)

ANONYMOUS = "anonymous"

# Big Buck Bunny on the Blender channel: long-lived, public, not embed- or
# age-restricted, with adaptive formats up to 4K.
_DEFAULT_URLS = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
CANARY_URLS = [u.strip() for u in environ.get("CANARY_URLS", _DEFAULT_URLS).split(",") if u.strip()]
CANARY_INTERVAL_MINUTES = int(environ.get("CANARY_INTERVAL_MINUTES", "180"))
CANARY_PROBE_BYTES = int(environ.get("CANARY_PROBE_BYTES", str(2 * 1024 * 1024)))

_probes = meter.create_counter("mediarvester.canary.probe", description="Canary probes, by outcome and error kind")
_set_healthy = create_cached_gauge(
    "mediarvester.canary.healthy", "1 when the latest anonymous canary probe downloaded data, 0 when it failed"
)
_set_cookies_healthy = create_cached_gauge(
    "mediarvester.canary.cookies_healthy",
    "1 when every cookies owner's file is a login and its latest probe downloaded data, 0 otherwise",
)


class _EnoughData(Exception):
    """Raised from the progress hook to stop a probe once data is flowing."""


@dataclass
class ProbeResult:
    url: str
    ok: bool
    kind: str | None = None
    message: str | None = None
    bytes: int = 0


@dataclass
class IdentityResult:
    identity: str
    checked_at: datetime
    ok: bool
    # None for the anonymous identity (no cookies involved).
    logged_in_before: bool | None = None
    logged_in_after: bool | None = None
    probes: list[ProbeResult] = field(default_factory=list)


_results: dict[str, IdentityResult] = {}
_lock = threading.Lock()


class _CaptureLogger:
    def __init__(self) -> None:
        self.last_error: str | None = None

    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        self.last_error = msg

    def error(self, msg: str) -> None:
        self.last_error = msg


def probe(url: str, cookiefile: str | None, workdir: str) -> ProbeResult:
    log = _CaptureLogger()
    received = {"bytes": 0}

    def hook(d: dict) -> None:
        received["bytes"] = max(received["bytes"], d.get("downloaded_bytes") or 0)
        if received["bytes"] >= CANARY_PROBE_BYTES:
            raise _EnoughData

    # Same YouTube options and format rule as a real download
    # (services/downloader.py _build_opts), minus everything about files.
    opts = {
        **youtube_opts(),
        "format": "bv*+ba",
        "outtmpl": f"{workdir}/%(id)s.%(ext)s",
        "progress_hooks": [hook],
        "logger": log,
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        "writeinfojson": False,
        "writethumbnail": False,
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except _EnoughData:
        return ProbeResult(url=url, ok=True, bytes=received["bytes"])
    except Exception as exc:  # a probe must never take the scheduler down
        message = str(exc) or log.last_error
        return ProbeResult(url=url, ok=False, kind=classify(message).value, message=message, bytes=received["bytes"])
    # Finished without hitting the threshold: a tiny video downloaded in full.
    if received["bytes"]:
        return ProbeResult(url=url, ok=True, bytes=received["bytes"])
    message = log.last_error or "no data received"
    return ProbeResult(url=url, ok=False, kind=classify(message).value, message=message)


def _probe_identity(identity: str, cookies_path: Path | None) -> IdentityResult:
    with tempfile.TemporaryDirectory(prefix="mediarvester-canary-") as workdir:
        cookie_copy = None
        before = after = None
        if cookies_path:
            cookie_copy = str(Path(workdir) / "cookies.txt")
            shutil.copyfile(cookies_path, cookie_copy)
            before = cookies_health.is_logged_in(cookie_copy)
        probes = [probe(url, cookie_copy, workdir) for url in CANARY_URLS]
        if cookie_copy:
            after = cookies_health.is_logged_in(cookie_copy)
    return IdentityResult(
        identity=identity,
        checked_at=datetime.utcnow(),
        ok=all(p.ok for p in probes),
        logged_in_before=before,
        logged_in_after=after,
        probes=probes,
    )


def _cookie_owners(cookies_root: str) -> dict[str, Path]:
    root = Path(cookies_root)
    if not root.is_dir():
        return {}
    return {p.stem: p for p in sorted(root.glob("*.txt")) if p.stem != ANONYMOUS}


def run_once(cookies_root: str) -> dict[str, IdentityResult]:
    """Probe anonymously and once per cookies owner. Blocking -- run it in a thread."""
    identities: list[tuple[str, Path | None]] = [(ANONYMOUS, None), *_cookie_owners(cookies_root).items()]
    fresh = {}
    for identity, path in identities:
        result = _probe_identity(identity, path)
        fresh[identity] = result
        for p in result.probes:
            _probes.add(
                1,
                {
                    "outcome": "ok" if p.ok else "failed",
                    "kind": p.kind or "none",
                    "identity": "anonymous" if identity == ANONYMOUS else "cookies",
                },
            )
        if result.ok:
            logger.info("canary %s: ok (logged_in=%s->%s)", identity, result.logged_in_before, result.logged_in_after)
        else:
            failed = next(p for p in result.probes if not p.ok)
            logger.warning("canary %s: FAILED [%s] %s: %s", identity, failed.kind, failed.url, failed.message)
        if path and result.logged_in_before and result.logged_in_after is False:
            logger.warning("canary %s: cookies stopped being a login during the probe (rotated by YouTube)", identity)

    with _lock:
        # Drop owners whose cookies file is gone.
        _results.clear()
        _results.update(fresh)
    _publish_gauges(fresh)
    return fresh


def _publish_gauges(results: dict[str, IdentityResult]) -> None:
    anonymous = results.get(ANONYMOUS)
    if anonymous:
        _set_healthy(1 if anonymous.ok else 0)
    owners = [r for i, r in results.items() if i != ANONYMOUS]
    if owners:
        _set_cookies_healthy(1 if all(r.ok and r.logged_in_after for r in owners) else 0)


def latest(identity: str) -> IdentityResult | None:
    with _lock:
        return _results.get(identity)


def latest_as_dict(identity: str) -> dict | None:
    result = latest(identity)
    return asdict(result) if result else None


def failing_kind() -> ErrorKind | None:
    """The error kind when the latest anonymous probe failed, else None."""
    result = latest(ANONYMOUS)
    if not result or result.ok:
        return None
    failed = next(p for p in result.probes if not p.ok)
    return ErrorKind(failed.kind) if failed.kind else ErrorKind.OTHER
