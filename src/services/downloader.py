import asyncio
import glob
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from logging import getLogger
from os import environ
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp
from opentelemetry.metrics import Observation
from opentelemetry.trace import Status, StatusCode

from sqlalchemy import delete, select, update

from db import Download, MediaItem, async_session
from services.episode_naming import resolve_episode
from services.telemetry import meter, propagate_context, tracer

logger = getLogger(__name__)

MEDIA_ROOT = environ.get("MEDIA_ROOT", "/app/downloads")
COOKIES_ROOT = environ.get("COOKIES_ROOT", "/app/cookies")
CONCURRENCY = int(environ.get("DOWNLOAD_CONCURRENCY", "2"))

# The UI placeholder shown for the few seconds between a download starting
# and yt-dlp's progress hook first reporting a real title (extraction/PO
# Token exchange happens before that). Deliberately excluded from the
# in_progress OTel gauge below -- it's a meaningless, throwaway label value
# to a metrics backend (a new Mimir series per occurrence for a string
# nobody queries on), not something worth graphing.
_RESOLVING_TITLE_PLACEHOLDER = "(resolving title...)"

_downloads_started = meter.create_counter(
    "mediarvester.download.started", description="Downloads that began running"
)
_downloads_completed = meter.create_counter(
    "mediarvester.download.completed", description="Downloads that reached a terminal state"
)
_download_duration = meter.create_histogram(
    "mediarvester.download.duration", unit="s", description="Wall-clock time from start to terminal state"
)
_download_retries = meter.create_counter(
    "mediarvester.download.retry", description="Auto-retries scheduled after a failed item"
)
_downloads_active = meter.create_up_down_counter(
    "mediarvester.download.active", description="Downloads currently running (bounded by DOWNLOAD_CONCURRENCY)"
)

# How many times a failed item gets automatically requeued before we give up
# on it for good, and how long to wait before each attempt. The first couple
# of delays are short, for a genuinely transient blip (a network hiccup, a
# one-off format hiccup). But YouTube's own rate-limit message is explicitly
# "for up to an hour" -- the original [120, 600, 1800] schedule kept every
# attempt inside that same hour, so a real rate-limit trip could burn all 3
# retries against the same still-active limit and give up for good having
# never actually gotten past it (confirmed happening for real: MrDeriv's
# VBssNWJl-bo, 2026-08-21). The last two tiers are deliberately pushed well
# past that window so a real rate limit -- or a same-day A/B-test-style
# block -- has actually cleared by the time they fire.
_MAX_AUTO_RETRIES = 4
_RETRY_DELAYS_SECONDS = [120, 1800, 3 * 3600, 24 * 3600]  # 2min, 30min, 3h, 1d


async def supersede_error_rows(session, url: str, owner: str, exclude_id: int) -> None:
    """Mark this owner's earlier failed attempts at `url` as "retried" now
    that a newer attempt (auto-retry, missed-retry recovery, or a manual
    resubmit) exists for it -- an "error" filter should only ever show the
    one attempt that's actually still unresolved, not the whole retry
    history piling up as separate rows (each retry already gets its own row
    by design, so the history was otherwise never pruned until something
    finally succeeded). `_on_success`'s cleanup still deletes "retried" rows
    outright once that happens. Caller commits."""
    await session.execute(
        update(Download)
        .where(Download.url == url, Download.owner == owner, Download.status == "error", Download.id != exclude_id)
        .values(status="retried")
    )


def is_probably_collection_url(url: str) -> bool:
    """Shared with api/downloads.py (dedupe policy) -- also gates the
    playlist-ordering pre-pass below, since that only makes sense for a
    collection URL and would just be a wasted extra request for a plain
    video link."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/").lower()
    query = parse_qs(parsed.query)

    if "list" in query:
        return True
    if path.endswith("/playlist"):
        return True
    if path.endswith(("/videos", "/shorts", "/streams")):
        return True
    if path.startswith(("/channel/", "/user/", "/c/", "/@")):
        return True
    return False


class _YtDlpLogAdapter:
    """Routes yt-dlp's own internal reporting through our logger instead of a
    raw, unformatted write to stderr -- which is what it does by default even
    with quiet=True (quiet only silences status/progress output, not
    warnings/errors). Without this, per-entry failure reasons ("Requested
    format is not available", 403s, etc.) are invisible to LOG_LEVEL and
    impossible to correlate with a download id.

    Also remembers the last warning/error it saw (`last_error`) -- when
    `ignoreerrors=True` swallows a per-entry failure, yt-dlp's own
    extract_info() just returns a falsy result with no exception at all, so
    the real reason (rate-limited, 403, a specific format unavailable...)
    would otherwise only ever exist as this log line, disconnected from the
    generic "no media extracted for <url>" that actually reaches the DB's
    error column and the UI. _run folds it back in when that happens.
    """

    def __init__(self, download_id: int) -> None:
        self._download_id = download_id
        self.last_error: str | None = None

    def debug(self, msg: str) -> None:
        # yt-dlp routes its normal status/progress chatter here too (as
        # "screen" output) when a logger is set -- keep it at debug so it's
        # opt-in via LOG_LEVEL rather than always-on noise.
        logger.debug("download %d: %s", self._download_id, msg)

    def warning(self, msg: str) -> None:
        self.last_error = msg
        logger.warning("download %d: %s", self._download_id, msg)

    def error(self, msg: str) -> None:
        self.last_error = msg
        logger.error("download %d: %s", self._download_id, msg)


def get_cookies_path(owner: str) -> str | None:
    """Return the cookies file for a user, falling back to the global one."""
    user_path = Path(COOKIES_ROOT) / f"{owner}.txt"
    if user_path.exists():
        return str(user_path)
    global_path = environ.get("YT_DLP_COOKIES_PATH")
    if global_path and Path(global_path).exists():
        return global_path
    return None


def _iter_downloaded_entries(info: dict) -> list[dict]:
    """Return the info dict for every entry a download attempted.

    A single-video download is one entry: `info` itself. A collection
    (playlist/channel) download instead nests one info dict per item under
    `entries` -- entries that failed extraction are `None` (ignoreerrors) and
    are skipped here. Whether an entry actually produced a usable file is
    decided separately in `_build_media_item`: `requested_downloads` can be
    present with no real file behind it when the download itself failed
    after format selection (e.g. a `.part` left stuck mid-transfer), so its
    mere presence here isn't proof of a completed download.
    """
    if "entries" in info:
        return [e for e in (info.get("entries") or []) if e]
    return [info]


def _flat_extract_info(url: str, owner: str) -> dict:
    """Cheap, download-free metadata lookup -- no format resolution, no PO
    Token cost. Shared by `extract_flat_entries` (collections) and
    `matching_known_playlist` (a single video's own id/uploader, to check it
    against a known playlist's membership)."""
    opts = {
        "js_runtimes": {"node": {}},
        "extractor_args": {"youtube": {"player_client": ["mweb"]}},
        "extract_flat": True,
        "ignoreerrors": True,
        "quiet": True,
    }
    if cookies := get_cookies_path(owner):
        opts["cookiefile"] = cookies
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


def extract_flat_entries(url: str, owner: str) -> list[dict] | None:
    """Cheap, download-free listing of a collection's entries. Shared by two
    callers: computing download order before a collection download starts
    (see `_ordered_playlist_items`), and `matching_known_playlist` checking
    whether a video belongs to an already-downloaded playlist. Returns None
    when yt-dlp reports no `entries` at all -- i.e. `url` wasn't actually a
    collection, so callers should skip whatever they were about to do with
    it rather than treat an empty list as "0 items"."""
    return _flat_extract_info(url, owner).get("entries")


_playlist_matches = meter.create_counter(
    "mediarvester.poller.playlist_match",
    description="Folder-placement lookups for a video, by whether a known playlist matched",
)


async def matching_known_playlist(owner: str, entry: dict) -> str | None:
    """If `entry` (a single video -- newly discovered by the poller, or a
    plain URL resubmitted through the API) is already part of a playlist
    this owner has previously downloaded in full, return that playlist's
    title so the video can join it in the library instead of landing loose
    in the creator's flat root folder -- see `folder_hint` below.

    A followed *channel* (or a bare video URL) has no notion of "this
    upload also belongs to playlist X" on its own -- that's not a property
    of the video, only discoverable by walking the playlist itself. So this
    only fires, and only costs anything, when the creator has at least one
    completed collection download on record; it stops at the first match
    rather than checking every known playlist.
    """
    video_id = entry.get("id")
    creator = entry.get("uploader") or entry.get("channel") or entry.get("creator")
    if not video_id or not creator:
        return None

    async with async_session() as session:
        result = await session.execute(
            select(Download.url, Download.title)
            .where(Download.owner == owner)
            .where(Download.creator == creator)
            .where(Download.status == "done")
            .order_by(Download.finished_at.desc())
            .limit(20)
        )
        rows = result.all()

    seen_urls: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for playlist_url, playlist_title in rows:
        if not playlist_title or playlist_url in seen_urls:
            continue
        if not is_probably_collection_url(playlist_url):
            continue
        seen_urls.add(playlist_url)
        candidates.append((playlist_url, playlist_title))
    if not candidates:
        return None

    with tracer.start_as_current_span("playlist_membership_check") as span:
        span.set_attribute("candidate_count", len(candidates))
        loop = asyncio.get_event_loop()
        for playlist_url, playlist_title in candidates:
            try:
                members = await loop.run_in_executor(
                    None, propagate_context(extract_flat_entries), playlist_url, owner
                )
            except Exception as exc:
                logger.debug(
                    "could not check whether %s belongs to known playlist %s: %s", video_id, playlist_url, exc
                )
                continue
            if members and any((member or {}).get("id") == video_id for member in members):
                logger.info(
                    "video %s matches known playlist %r, placing it alongside that playlist",
                    video_id, playlist_title,
                )
                _playlist_matches.add(1, {"matched": True})
                return playlist_title
        _playlist_matches.add(1, {"matched": False})
    return None


def _ordered_playlist_items(entries: list[dict]) -> tuple[str, dict[int, int]]:
    """Compute a yt-dlp `playlist_items` spec that downloads entries in
    ascending episode order instead of whatever order the platform returns
    them in -- a creator's "newest first" playlist would otherwise download
    the finale before episode 1, forcing a full-playlist wait before there's
    anything watchable. Reuses the same episode-number heuristic already
    trusted for on-disk renaming (episode_naming.resolve_episode); entries
    with no resolvable number fall back to their original position, so they
    sort in amongst the numbered ones roughly where the platform put them
    rather than all clumping at one end.

    yt-dlp processes `playlist_items` entries in exactly the order given
    (verified against yt-dlp's own PlaylistEntries.get_requested_items --
    it's a straight iteration over the parsed spec, not re-sorted), so a
    comma-separated list of original 1-based indices in our desired order is
    all that's needed -- no restructuring of the download call itself.

    Returns `(playlist_items_string, position_by_original_index)`: the
    string for `_build_opts`, and a map from each entry's *original* index
    (what yt-dlp's progress hook reports as `playlist_index`) to its
    position in the new order, so the UI can show "N of M in download
    order" instead of the platform's own numbering.
    """
    numbered: list[tuple[int, int]] = []
    for i, entry in enumerate(entries, start=1):
        if not entry:
            continue  # failed extraction entirely; yt-dlp won't attempt it regardless
        resolved = resolve_episode(entry)
        number = resolved[0] if resolved else i
        numbered.append((number, i))
    numbered.sort()

    playlist_items = ",".join(str(original_index) for _, original_index in numbered)
    position_by_original_index = {
        original_index: position
        for position, (_, original_index) in enumerate(numbered, start=1)
    }
    return playlist_items, position_by_original_index


def _apply_episode_prefix(abs_path: str, entry: dict) -> str:
    """Rename a just-downloaded file (and its sidecars) to `N - Title.ext`
    when an episode number can be resolved, so the on-disk name is right
    from the first write -- no separate manual renaming pass needed."""
    resolved = resolve_episode(entry)
    if resolved is None:
        return abs_path
    number, title = resolved

    src = Path(abs_path)
    title = (title or src.stem).replace("/", "-").strip()
    new_stem = f"{number} - {title}"
    if new_stem == src.stem:
        return abs_path
    new_path = src.with_name(f"{new_stem}{src.suffix}")

    try:
        src.rename(new_path)
    except OSError as exc:
        logger.warning("could not apply episode prefix to %s: %s", src.name, exc)
        return abs_path

    # yt-dlp writes .info.json / thumbnail sidecars next to the video under
    # the same stem (e.g. "title.info.json", "title.webp") -- move them along
    # so they don't end up orphaned under the old name.
    stem = src.stem
    for name in os.listdir(src.parent):
        if name == src.name or not name.startswith(stem):
            continue
        rest = name[len(stem) :]
        if not rest.startswith("."):
            continue  # shares a prefix but isn't actually a sidecar of this file
        try:
            (src.parent / name).rename(src.parent / f"{new_stem}{rest}")
        except OSError as exc:
            logger.warning("could not move sidecar %s alongside renamed episode: %s", name, exc)

    return str(new_path)


def _verify_downloaded_file(path: str, expected_duration: float | None) -> str | None:
    """Confirm a file yt-dlp reported as finished is actually a complete,
    readable media file -- not a truncated write (disk full mid-merge, a
    process kill) or a container ffmpeg produced but couldn't finish
    properly. Existence alone (the only check before this) doesn't catch
    either case. Returns None when the file looks fine, or a short reason
    string when it doesn't -- callers treat that the same as "no file was
    produced at all" (failed entry, eligible for auto-retry) and remove the
    bad file rather than leave a corrupt one in the library."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"ffprobe could not run: {exc}"
    if result.returncode != 0:
        return f"ffprobe rejected the file: {result.stderr.strip()[:200]}"
    try:
        actual_duration = float(result.stdout.strip())
    except ValueError:
        return "ffprobe reported no readable duration"
    if actual_duration <= 0:
        return "reported duration is zero"
    # Some slack for container/timestamp rounding -- this is a sanity check
    # against a badly truncated file, not a frame-accurate comparison.
    if expected_duration and actual_duration < expected_duration * 0.9:
        return (
            f"duration {actual_duration:.0f}s is well short of the "
            f"expected {expected_duration:.0f}s"
        )
    return None


def _cleanup_stray_fragments(original_filepath: str) -> None:
    """yt-dlp writes per-format temp fragments alongside the final output
    (e.g. `<title>.f299.mp4.part`, an audio-only `<title>.f251.webm` while
    waiting to mux with a video track that never finished) and normally
    removes them itself once a download completes or is cleanly aborted.
    A process kill mid-transfer (a pod OOM, a redeploy) skips that cleanup
    entirely, and nothing else ever revisits the row to try again -- these
    survive indefinitely. Confirmed happening for real: a `.f299.mp4.part`
    + orphaned `.f251.webm` for the same video survived several retries
    before being found and removed by hand (2026-08-21/22, MrDeriv library
    cleanup). Safe regardless of whether this entry ultimately succeeded or
    failed -- a legitimate final output is never named `<stem>.f<N>.*`."""
    if not original_filepath:
        return
    original = Path(original_filepath)
    parent = original.parent
    if not parent.is_dir():
        return
    for candidate in parent.glob(f"{glob.escape(original.stem)}.f[0-9]*"):
        try:
            candidate.unlink()
            logger.info("removed stray incomplete fragment: %s", candidate.name)
        except OSError as exc:
            logger.warning("could not remove stray fragment %s: %s", candidate.name, exc)


class Downloader:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=CONCURRENCY)
        self._loop: asyncio.AbstractEventLoop | None = None
        # download_id -> {original playlist_index: position in download order}.
        # Populated in _run before a collection download starts, read by
        # _progress_hook while it's in flight, and popped once it finishes --
        # see _ordered_playlist_items for why this exists.
        self._index_maps: dict[int, dict[int, int]] = {}
        # download_id -> {"owner": ..., "title": ...} for whatever is running
        # right now. Feeds the "download.in_progress" gauge (a Grafana
        # state-timeline, same idea as Jellyfin's now-playing panel), so it's
        # updated straight from this thread rather than round-tripped through
        # the DB -- title becomes accurate as soon as _progress_hook sees it.
        self._in_progress: dict[int, dict[str, str]] = {}
        self._in_progress_lock = threading.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def enqueue(
        self,
        download_id: int,
        url: str,
        owner: str = "anonymous",
        force: bool = False,
        folder_hint: str | None = None,
    ) -> None:
        self._executor.submit(propagate_context(self._run), download_id, url, owner, force, folder_hint)

    def _run(
        self, download_id: int, url: str, owner: str, force: bool = False, folder_hint: str | None = None
    ) -> None:
        logger.info("download %d starting: %s", download_id, url)

        is_collection = is_probably_collection_url(url)
        started_at = time.monotonic()
        _downloads_started.add(1, {"is_collection": is_collection})
        _downloads_active.add(1)
        with self._in_progress_lock:
            self._in_progress[download_id] = {"owner": owner, "title": _RESOLVING_TITLE_PLACEHOLDER}
        status = "error"
        platform = "unknown"

        with tracer.start_as_current_span("download") as span:
            span.set_attribute("download_id", download_id)
            span.set_attribute("owner", owner)
            span.set_attribute("is_collection", is_collection)
            span.set_attribute("force", force)
            try:
                playlist_items = None
                if is_collection:
                    try:
                        entries = extract_flat_entries(url, owner)
                    except Exception as exc:
                        logger.warning(
                            "download %d: could not pre-list collection for ordering, "
                            "falling back to the platform's own order: %s",
                            download_id, exc,
                        )
                        entries = None
                    if entries:
                        playlist_items, position_map = _ordered_playlist_items(entries)
                        self._index_maps[download_id] = position_map
                elif folder_hint is None and self._loop:
                    # A single video with no folder_hint already set -- either
                    # a plain URL submitted through the API, or an auto-retry
                    # of one. The poller computes this for videos it discovers
                    # itself (see poller.py's matching_known_playlist call),
                    # but a manually (re)submitted URL never went through
                    # that path, so without this it would land loose in the
                    # creator's flat root folder even when it's actually part
                    # of a playlist already downloaded in full -- exactly what
                    # made a mess of MrDeriv's library (2026-08-21).
                    try:
                        entry_info = _flat_extract_info(url, owner)
                    except Exception as exc:
                        logger.debug(
                            "download %d: could not pre-fetch metadata, "
                            "continuing without a folder hint: %s",
                            download_id, exc,
                        )
                        entry_info = {}
                    if entry_info.get("title"):
                        # Stashed now, ahead of the real download attempt, so a
                        # row that fails before yt-dlp's progress hook ever
                        # fires (e.g. an immediate rate-limit) still has a
                        # human-readable title instead of a bare URL --
                        # dl.title is only ever set on success otherwise, and
                        # current_title (the other fallback _on_error checks)
                        # would stay empty for exactly this case.
                        asyncio.run_coroutine_threadsafe(
                            self._set_current_title(download_id, entry_info["title"]), self._loop
                        )
                    if entry_info:
                        try:
                            folder_hint = asyncio.run_coroutine_threadsafe(
                                matching_known_playlist(owner, entry_info), self._loop
                            ).result(timeout=30)
                        except Exception as exc:
                            logger.debug(
                                "download %d: could not check playlist membership, "
                                "continuing without a folder hint: %s",
                                download_id, exc,
                            )

                log_adapter = _YtDlpLogAdapter(download_id)
                opts = self._build_opts(download_id, owner, force, playlist_items, folder_hint, log_adapter)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                if not info:
                    # ignoreerrors makes yt-dlp swallow a total failure on a single
                    # item (e.g. rate-limited/unavailable video) and return None
                    # instead of raising -- don't record that as a success. yt-dlp
                    # itself never raises here, so the *real* reason (rate-limited,
                    # 403, a specific format unavailable...) only ever existed as a
                    # log line via log_adapter -- fold it back in, or this and the
                    # DB's error column both end up with nothing more specific than
                    # "no media extracted for <url>".
                    reason = log_adapter.last_error
                    message = f"no media extracted for {url}"
                    if reason:
                        message = f"{message}: {reason}"
                    raise yt_dlp.utils.DownloadError(message)
                platform = info.get("extractor") or "unknown"
                status = "done"
                self._schedule(self._on_success(download_id, info, owner))
            except Exception as exc:
                logger.error(
                    "download %d failed (owner=%s, url=%s): %s", download_id, owner, url, exc
                )
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                self._schedule(self._on_error(download_id, str(exc)))
            finally:
                self._index_maps.pop(download_id, None)
                with self._in_progress_lock:
                    self._in_progress.pop(download_id, None)

        _download_duration.record(
            time.monotonic() - started_at, {"status": status, "is_collection": is_collection}
        )
        _downloads_completed.add(1, {"status": status, "platform": platform})
        _downloads_active.add(-1)

    def _build_opts(
        self,
        download_id: int,
        owner: str,
        force: bool = False,
        playlist_items: str | None = None,
        folder_hint: str | None = None,
        log_adapter: "_YtDlpLogAdapter | None" = None,
    ) -> dict:
        # A folder_hint (set by the poller when a newly-discovered video
        # matches an already-downloaded playlist -- see poller.py) replaces
        # the %(playlist_title,playlist|)s segment with a fixed literal,
        # since a lone video URL carries no playlist context of its own for
        # yt-dlp to resolve there. "%" is yt-dlp's own template escape and
        # "/" would otherwise inject an extra path level, so both are
        # neutralised the same way _apply_episode_prefix already sanitises
        # titles for the filesystem.
        playlist_segment = (
            folder_hint.replace("%", "%%").replace("/", "-") if folder_hint else "%(playlist_title,playlist|)s"
        )
        opts: dict = {
            # Use Node.js for YouTube's n-challenge (requires yt-dlp-ejs + Node 22+).
            # yt-dlp defaults to deno-only; node must be explicitly enabled.
            "js_runtimes": {"node": {}},
            # YouTube's "web"/default clients (and the ones yt-dlp auto-selects,
            # e.g. android_vr) increasingly serve SABR-only streams with no
            # direct HTTPS format URL yt-dlp can download -- see
            # https://github.com/yt-dlp/yt-dlp/issues/12482. mweb is the
            # maintainer-recommended client that still exposes real HTTPS
            # formats when paired with a PO Token (bgutil-ytdlp-pot-provider
            # sidecar); verified empirically 2026-08-19 to complete a full
            # download with zero cookies. cookiefile below still gets applied
            # on top when present, which is what age-restricted/members-only
            # content needs -- mweb doesn't remove that requirement, it just
            # means most public videos no longer depend on cookie freshness.
            "extractor_args": {"youtube": {"player_client": ["mweb"]}},
            # No explicit "format" override -- yt-dlp's default
            # (bestvideo*+bestaudio/best) picks full adaptive/HD quality.
            # 2026-08-20/21 incident: YouTube's "bind GVS PO Token to video
            # ID" A/B test 403'd every adaptive request through mweb+bgutil;
            # briefly worked around here by forcing the 360p progressive
            # stream, which is exactly the quality regression this project
            # doesn't want to ship. Fixed properly by the pot-provider sidecar
            # image bump to 1.3.2 (mints tokens from the homepage challenge +
            # ytcfg -- see stack/values/default/media/mediarvester.yaml and
            # docs/incidents/2026-08-20-youtube-403-and-oom.md), confirmed to
            # restore full adaptive downloads. Left un-overridden here so a
            # future site-side issue surfaces as a visible failure (and the
            # /api/settings/service-status banner) rather than silently
            # degrading everyone's quality again.
            # Skip unavailable/age-restricted/private items instead of aborting the whole job.
            # Critical for playlists that mix public and restricted content.
            "ignoreerrors": True,
            # Platform-agnostic tree: creator / [playlist /] title
            # %(uploader,channel,creator|Unsorted)s  — first non-empty of those three fields
            # %(playlist_title,playlist|)s           — playlist name, or empty string
            # When playlist is absent the empty component produces a double slash,
            # which POSIX normalises to a single slash.  _on_success also runs the
            # resolved filepath through pathlib.Path to strip any residual //.
            "outtmpl": (
                f"{MEDIA_ROOT}"
                "/%(uploader,channel,creator|Unsorted)s"
                f"/{playlist_segment}"
                "/%(title)s.%(ext)s"
            ),
            "writeinfojson": True,
            "writethumbnail": True,
            "progress_hooks": [lambda d: self._progress_hook(d, download_id)],
            # Routes all of yt-dlp's own reporting (including what quiet/
            # no_warnings would otherwise suppress or print raw to stderr)
            # through our logger instead -- see _YtDlpLogAdapter.
            "logger": log_adapter or _YtDlpLogAdapter(download_id),
            "quiet": True,
            "noprogress": True,
            # See services/poller.py -- unthrottled per-entry requests (e.g. a
            # playlist URL submitted directly) are what trip YouTube's rate
            # limiter in the first place. This spaces out the extraction
            # requests yt-dlp makes internally while walking a playlist/
            # channel URL passed straight to a download (the poller's own
            # sleep_interval_requests only covers its separate metadata scan,
            # not this path).
            "sleep_interval_requests": 1,
            # ...and this spaces out the actual per-video downloads within a
            # playlist/channel download, for the same reason.
            "sleep_interval": 2,
        }
        if playlist_items:
            opts["playlist_items"] = playlist_items
        if force:
            # Recovery after a restart: an interrupted job may have left a partial
            # `.part` (or a half-written final) file on disk. Don't resume it — its
            # integrity is unknown — discard and overwrite from scratch instead.
            opts["continuedl"] = False
            opts["overwrites"] = True
        if cookies := get_cookies_path(owner):
            opts["cookiefile"] = cookies
        if user := environ.get("YT_DLP_USERNAME"):
            opts["username"] = user
        if pwd := environ.get("YT_DLP_PASSWORD"):
            opts["password"] = pwd
        return opts

    def _progress_hook(self, d: dict, download_id: int) -> None:
        # For a playlist/channel download, yt-dlp re-invokes this hook per
        # entry with the current entry's own info dict -- that's where the
        # entry's position and title within the collection live. A single-
        # video download has no playlist_index/playlist_count, which is how
        # the update below tells the two cases apart.
        info = d.get("info_dict") or {}
        playlist_index = info.get("playlist_index")
        playlist_count = info.get("playlist_count") or info.get("n_entries")
        title = info.get("title")

        # Translate yt-dlp's *original* playlist position into our chosen
        # download-order position (see _ordered_playlist_items) so "N of M"
        # counts up 1, 2, 3... in the order files are actually landing,
        # rather than jumping around in the platform's own numbering.
        position_map = self._index_maps.get(download_id)
        if position_map:
            playlist_count = len(position_map)
            if playlist_index in position_map:
                playlist_index = position_map[playlist_index]

        if title:
            with self._in_progress_lock:
                entry = self._in_progress.get(download_id)
                if entry:
                    entry["title"] = title

        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            progress = (downloaded / total * 100) if total else 0.0
            self._schedule(
                self._update_progress(download_id, progress, playlist_index, playlist_count, title)
            )
        elif d["status"] == "finished":
            self._schedule(
                self._update_progress(
                    download_id, 100.0, playlist_index, playlist_count, title, entry_finished=True
                )
            )

    def _schedule(self, coro) -> None:
        if self._loop:
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _update_progress(
        self,
        download_id: int,
        progress: float,
        playlist_index: int | None,
        playlist_count: int | None,
        title: str | None,
        entry_finished: bool = False,
    ) -> None:
        async with async_session() as session:
            dl = await session.get(Download, download_id)
            if not dl:
                return
            dl.progress = progress
            dl.status = "downloading"
            if playlist_count and not dl.total_entries:
                logger.info("download %d: collection detected, %d item(s)", download_id, playlist_count)
            if playlist_count:
                dl.total_entries = playlist_count
            if playlist_index:
                dl.current_index = playlist_index
            if title:
                dl.current_title = title
            if entry_finished and title:
                completed = list(dl.completed_items or [])
                if title not in completed:
                    completed.append(title)
                    dl.completed_items = completed
                    if playlist_count:
                        logger.info(
                            "download %d: finished %d/%d - %s",
                            download_id, len(completed), playlist_count, title,
                        )
            await session.commit()

    async def _set_current_title(self, download_id: int, title: str) -> None:
        async with async_session() as session:
            dl = await session.get(Download, download_id)
            if dl and not dl.current_title:
                dl.current_title = title
                await session.commit()

    async def _on_success(self, download_id: int, info: dict, owner: str) -> None:
        async with async_session() as session:
            dl = await session.get(Download, download_id)
            if not dl:
                return

            # "entries" includes an entry per playlist item yt-dlp attempted,
            # even ones that failed extraction entirely (those come back as
            # None with ignoreerrors=True) -- count against this, not the
            # already-filtered list below, so a fully-failed playlist reports
            # "8 entries attempted" instead of a misleading "0".
            attempted_count = len(info["entries"]) if "entries" in info else 1
            items: list[MediaItem] = []
            failed_entries: list[dict] = []
            for entry in _iter_downloaded_entries(info):
                item = self._build_media_item(entry, owner, download_id, dl.url)
                if item:
                    items.append(item)
                else:
                    failed_entries.append(entry)

            retry_count = dl.retry_count
            source_id = dl.source_id

            if not items:
                # ignoreerrors=True means a playlist/channel download where
                # every entry individually failed (e.g. rate-limited, 403s,
                # or a per-entry download that never got past a `.part` file)
                # still returns a truthy top-level `info` -- without this
                # check that gets recorded as "done" despite nothing actually
                # landing on disk.
                logger.error(
                    "download %d: no files were successfully downloaded (%d entries attempted)",
                    download_id, attempted_count,
                )
                dl.status = "error"
                dl.error = "no files were successfully downloaded"
                dl.finished_at = datetime.utcnow()
                dl.retry_at = self._compute_retry_at(retry_count)
                await session.commit()
                self._schedule_retries(download_id, failed_entries, owner, source_id, retry_count)
                return

            dl.status = "done"
            dl.progress = 100.0
            dl.finished_at = datetime.utcnow()
            dl.title = info.get("title")
            dl.platform = info.get("extractor")
            # Same fallback chain as the outtmpl folder name below, so
            # "creator" reflects the same identity the file actually landed
            # under regardless of which field a given platform populates.
            # For a collection (playlist/channel tab), yt-dlp's top-level info
            # dict routinely has none of these -- they're per-entry fields --
            # so without the entries-fallback below, every collection's own
            # Download row ends up with creator=None and can never itself
            # serve as a `matching_known_playlist` candidate for later videos
            # from the same creator (confirmed empirically: every prior
            # collection download in this DB had creator=None).
            dl.creator = (
                info.get("uploader") or info.get("channel") or info.get("creator")
                or next(
                    (
                        e.get("uploader") or e.get("channel") or e.get("creator")
                        for e in (info.get("entries") or [])
                        if e
                    ),
                    None,
                )
            )
            dl.current_title = None

            for item in items:
                session.add(item)

            await session.commit()

            # A resubmit of the same URL just succeeded -- any earlier failed
            # attempts at it (manual retries or exhausted auto-retries alike,
            # including ones already downgraded to "retried" by
            # supersede_error_rows once a newer attempt existed) are no
            # longer telling the truth about the current state, so drop them
            # instead of leaving stale rows for something that's actually
            # fine now. Neither status ever has MediaItems attached, so this
            # can't orphan anything.
            result = await session.execute(
                delete(Download).where(
                    Download.url == dl.url,
                    Download.owner == owner,
                    Download.status.in_(("error", "retried")),
                    Download.id != download_id,
                )
            )
            await session.commit()
            if result.rowcount:
                logger.info(
                    "download %d done: cleared %d stale error row(s) for the same URL",
                    download_id, result.rowcount,
                )

            skipped = attempted_count - len(items)
            if skipped:
                logger.warning(
                    "download %d done: %d item(s) saved, %d skipped (see prior warnings)",
                    download_id, len(items), skipped,
                )
            else:
                logger.info("download %d done: %d item(s) saved", download_id, len(items))
            self._schedule_retries(download_id, failed_entries, owner, source_id, retry_count)

    def _build_media_item(
        self, entry: dict, owner: str, download_id: int, fallback_url: str
    ) -> MediaItem | None:
        requested = entry.get("requested_downloads") or [{}]
        abs_path = requested[0].get("filepath", "")
        # `filepath` can be present with the entry otherwise indicating the
        # download never actually completed (e.g. it's still sitting as a
        # `.part` file after a mid-transfer failure) -- checking existence
        # of the real, final path is what actually proves a file landed.
        if not abs_path or not Path(abs_path).exists():
            logger.warning(
                "download %d: entry %r produced no file, skipping",
                download_id, entry.get("title") or entry.get("webpage_url") or fallback_url,
            )
            _cleanup_stray_fragments(abs_path)
            return None

        problem = _verify_downloaded_file(abs_path, entry.get("duration"))
        if problem:
            logger.warning(
                "download %d: %r failed verification (%s) -- removing and treating as failed",
                download_id, entry.get("title") or fallback_url, problem,
            )
            try:
                Path(abs_path).unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("could not remove the bad file %s: %s", abs_path, exc)
            _cleanup_stray_fragments(abs_path)
            return None

        # Normalise away double slashes that arise when optional template
        # components (e.g. playlist) are absent and evaluate to "".
        abs_path = str(Path(abs_path))
        abs_path = _apply_episode_prefix(abs_path, entry)
        _cleanup_stray_fragments(requested[0].get("filepath", ""))
        local_path = os.path.relpath(abs_path, MEDIA_ROOT)

        thumbnail_path: str | None = None
        base = Path(MEDIA_ROOT) / local_path
        for ext in (".jpg", ".png", ".webp"):
            candidate = base.with_suffix(ext)
            if candidate.exists():
                thumbnail_path = os.path.relpath(str(candidate), MEDIA_ROOT)
                break

        return MediaItem(
            title=entry.get("title") or fallback_url,
            platform=entry.get("extractor"),
            source_url=entry.get("webpage_url") or fallback_url,
            local_path=local_path,
            thumbnail_path=thumbnail_path,
            duration_seconds=entry.get("duration"),
            owner=owner,
            download_id=download_id,
        )

    async def _on_error(self, download_id: int, error: str) -> None:
        async with async_session() as session:
            dl = await session.get(Download, download_id)
            if dl:
                # dl.title is only ever set on success, but a title may still
                # be known from a progress event that arrived before the
                # eventual failure (e.g. it started downloading, then failed
                # mid-transfer) -- worth keeping for the retry row below
                # rather than always falling back to a bare URL.
                title = dl.title or dl.current_title
                dl.status = "error"
                dl.error = error
                dl.finished_at = datetime.utcnow()
                dl.current_title = None
                dl.retry_at = self._compute_retry_at(dl.retry_count)
                await session.commit()
                logger.error(
                    "download %d recorded as error -- %r (creator=%s, platform=%s, owner=%s, url=%s): %s",
                    download_id, title or "(untitled)", dl.creator, dl.platform, dl.owner, dl.url, error,
                )
                self._schedule_retries(
                    download_id,
                    [{"webpage_url": dl.url, "title": title}],
                    dl.owner,
                    dl.source_id,
                    dl.retry_count,
                )

    def _compute_retry_at(self, current_retry_count: int) -> datetime | None:
        """None means "no further auto-retry will happen" -- either it's
        about to be scheduled fresh (caller checks the cap itself in
        _schedule_retries) or the cap's already been hit. Kept as its own
        method so both failure paths above compute this identically."""
        if current_retry_count >= _MAX_AUTO_RETRIES:
            return None
        return datetime.utcnow() + timedelta(seconds=_RETRY_DELAYS_SECONDS[current_retry_count])

    def _schedule_retries(
        self,
        download_id: int,
        failed_entries: list[dict],
        owner: str,
        source_id: int | None,
        current_retry_count: int,
    ) -> None:
        """Requeue every entry that didn't produce a file, after a delay --
        long enough that a transient site-side issue (rate limiting, a PO
        Token hiccup) has a real chance to clear rather than immediately
        re-tripping the same limit. Each retry is its own new Download row
        (visible in the queue, its own status) rather than silently mutating
        this one, and gives up for good after _MAX_AUTO_RETRIES so a
        genuinely broken/unavailable video doesn't retry forever."""
        if not failed_entries:
            return
        if current_retry_count >= _MAX_AUTO_RETRIES:
            logger.warning(
                "download %d: giving up on %d failed item(s) after %d retries",
                download_id, len(failed_entries), current_retry_count,
            )
            return

        delay = _RETRY_DELAYS_SECONDS[current_retry_count]
        for entry in failed_entries:
            retry_url = entry.get("webpage_url") or entry.get("url")
            if not retry_url:
                continue
            logger.info(
                "download %d: will retry %r in %ds (attempt %d/%d)",
                download_id, entry.get("title") or retry_url, delay,
                current_retry_count + 1, _MAX_AUTO_RETRIES,
            )
            _download_retries.add(1, {"attempt": str(current_retry_count + 1)})
            asyncio.create_task(
                self._retry_after_delay(
                    retry_url, owner, source_id, current_retry_count + 1, delay, entry.get("title")
                )
            )

    async def _retry_after_delay(
        self,
        url: str,
        owner: str,
        source_id: int | None,
        retry_count: int,
        delay: float,
        title: str | None = None,
    ) -> None:
        await asyncio.sleep(delay)
        async with async_session() as session:
            # Carrying the already-known title over means a retry row shows
            # something better than a bare URL immediately, rather than
            # waiting on this attempt to also succeed before it has one.
            dl = Download(url=url, owner=owner, source_id=source_id, retry_count=retry_count, title=title)
            session.add(dl)
            await session.commit()
            await session.refresh(dl)
            await supersede_error_rows(session, url, owner, dl.id)
            await session.commit()
        logger.info("download %d: auto-retry attempt %d for %s", dl.id, retry_count, url)
        self.enqueue(dl.id, url, owner)


downloader = Downloader()


def _observe_queue_depth(options):
    # Backlog of submitted-but-not-yet-running work against a pool bounded
    # by DOWNLOAD_CONCURRENCY (default 2) -- non-zero for any length of time
    # means downloads are piling up faster than they can run.
    yield Observation(downloader._executor._work_queue.qsize())


def _observe_in_progress(options):
    # One Observation per download currently running, labeled by owner+title
    # instead of a single count -- lets Grafana render this as a state-
    # timeline (a row per owner, a bar per download) the same way the
    # Jellyfin dashboard shows now-playing sessions. A download's row simply
    # stops appearing once it's popped from _in_progress, which is what
    # produces the gap between bars.
    with downloader._in_progress_lock:
        snapshot = list(downloader._in_progress.values())
    for entry in snapshot:
        if entry["title"] == _RESOLVING_TITLE_PLACEHOLDER:
            continue
        yield Observation(1, {"owner": entry["owner"], "title": entry["title"]})


meter.create_observable_gauge(
    "mediarvester.download.in_progress",
    callbacks=[_observe_in_progress],
    description="1 for each download currently running, labeled by owner and title",
)


meter.create_observable_gauge(
    "mediarvester.download.queue_depth",
    callbacks=[_observe_queue_depth],
    description="Downloads submitted but not yet running",
)


def warm_up_yt_dlp_plugins() -> None:
    """Construct one throwaway YoutubeDL() synchronously, before any worker
    thread gets a chance to start.

    yt-dlp's PO-Token provider plugins (bgutil-ytdlp-pot-provider) register
    themselves into a shared, process-wide registry dict the first time any
    YoutubeDL() is constructed -- see extractor/youtube/pot/_provider.py's
    register_provider_generic, which does a bare
    `assert key not in registry` with no lock around the check-then-set.
    recover_interrupted (below) can hand several downloads to the worker
    pool at once right at startup; if two DOWNLOAD_CONCURRENCY threads each
    construct their *first* YoutubeDL() at close to the same moment, the
    loser trips that assertion. It's harmless (the plugin that won the race
    registers fine and is what every download actually uses from then on --
    confirmed by it never recurring after the first hit each restart), but
    it logs a scary-looking "Error while importing module ... already
    registered" traceback. Doing this once, single-threaded, closes the
    race off entirely: the registry is already populated before anything
    concurrent can start.
    """
    try:
        yt_dlp.YoutubeDL({"quiet": True, "js_runtimes": {"node": {}}}).close()
    except Exception as exc:
        logger.warning("could not warm up yt-dlp's plugin registry: %s", exc)


async def recover_interrupted() -> None:
    """Re-enqueue downloads orphaned by a restart.

    Download workers live in an in-process ThreadPoolExecutor, so a redeploy
    leaves any `queued`/`downloading` row stuck forever — its thread is gone and
    nothing will ever move it to `done`/`error`. On startup, reset those rows and
    re-enqueue them with `force=True` so yt-dlp discards any partial file rather
    than resuming one of unknown integrity.
    """
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(Download).where(Download.status.in_(("downloading", "queued")))
        )
        stuck = result.scalars().all()
        for dl in stuck:
            dl.status = "queued"
            dl.progress = 0.0
            dl.error = None
            # force=True below discards any partial file, so a fresh run also
            # starts a fresh playlist position rather than showing stale counts.
            dl.current_index = None
            dl.total_entries = None
            dl.current_title = None
            dl.completed_items = None
        await session.commit()
        rows = [(dl.id, dl.url, dl.owner, dl.folder_hint) for dl in stuck]

    for download_id, url, owner, folder_hint in rows:
        downloader.enqueue(download_id, url, owner, force=True, folder_hint=folder_hint)
        logger.info("recovered interrupted download %d: %s", download_id, url)

    if rows:
        logger.info("re-enqueued %d interrupted download(s) after restart", len(rows))


async def recover_missed_retries() -> None:
    """Re-fire an auto-retry that a restart silently ate.

    _schedule_retries's delay is a bare in-process asyncio task (sleep +
    create_task on the running event loop) -- nothing durable backs it.
    recover_interrupted (above) only rescues rows stuck `queued`/
    `downloading`; a row already sitting in `error` with a future
    `retry_at` isn't stuck in either of those states, so a restart in the
    middle of that wait just loses the retry with no trace: no error, no
    log, nothing ever revisits it. Confirmed happening for real 2026-08-21
    (a redeploy landed 3 minutes before a scheduled retry).

    On startup, find every error row whose retry_at has already passed --
    _compute_retry_at already returns None once _MAX_AUTO_RETRIES is hit,
    so an exhausted row is naturally excluded -- and where nothing more
    recent exists for the same url+owner (i.e. the retry this row itself
    scheduled genuinely never happened, rather than having already fired
    and produced its own newer row, successful or not).
    """
    from sqlalchemy import func, select

    now = datetime.utcnow()
    async with async_session() as session:
        candidates = (
            await session.execute(
                select(Download)
                .where(Download.status == "error")
                .where(Download.retry_at.isnot(None))
                .where(Download.retry_at <= now)
            )
        ).scalars().all()

        rows = []
        for dl in candidates:
            latest_id = (
                await session.execute(
                    select(func.max(Download.id))
                    .where(Download.url == dl.url)
                    .where(Download.owner == dl.owner)
                )
            ).scalar_one()
            if latest_id == dl.id:
                rows.append((dl.id, dl.url, dl.owner, dl.source_id, dl.retry_count, dl.title))

    for download_id, url, owner, source_id, retry_count, title in rows:
        logger.warning(
            "download %d: a scheduled auto-retry for %s never fired (likely a restart mid-wait) -- firing it now",
            download_id, url,
        )
        async with async_session() as session:
            retry_dl = Download(url=url, owner=owner, source_id=source_id, retry_count=retry_count + 1, title=title)
            session.add(retry_dl)
            await session.commit()
            await session.refresh(retry_dl)
            await supersede_error_rows(session, url, owner, retry_dl.id)
            await session.commit()
        downloader.enqueue(retry_dl.id, url, owner)

    if rows:
        logger.info("recovered %d missed auto-retry(ies) after restart", len(rows))
