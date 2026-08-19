import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from logging import getLogger
from os import environ
from pathlib import Path

import yt_dlp

from db import Download, MediaItem, async_session
from services.episode_naming import resolve_episode

logger = getLogger(__name__)

MEDIA_ROOT = environ.get("MEDIA_ROOT", "/app/downloads")
COOKIES_ROOT = environ.get("COOKIES_ROOT", "/app/cookies")
CONCURRENCY = int(environ.get("DOWNLOAD_CONCURRENCY", "2"))


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


class Downloader:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=CONCURRENCY)
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def enqueue(self, download_id: int, url: str, owner: str = "anonymous", force: bool = False) -> None:
        self._executor.submit(self._run, download_id, url, owner, force)

    def _run(self, download_id: int, url: str, owner: str, force: bool = False) -> None:
        opts = self._build_opts(download_id, owner, force)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            if not info:
                # ignoreerrors makes yt-dlp swallow a total failure on a single
                # item (e.g. rate-limited/unavailable video) and return None
                # instead of raising -- don't record that as a success.
                raise yt_dlp.utils.DownloadError(f"no media extracted for {url}")
            self._schedule(self._on_success(download_id, info, owner))
        except Exception as exc:
            logger.error("download %d failed: %s", download_id, exc)
            self._schedule(self._on_error(download_id, str(exc)))

    def _build_opts(self, download_id: int, owner: str, force: bool = False) -> dict:
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
                "/%(playlist_title,playlist|)s"
                "/%(title)s.%(ext)s"
            ),
            "writeinfojson": True,
            "writethumbnail": True,
            "progress_hooks": [lambda d: self._progress_hook(d, download_id)],
            "quiet": True,
            "no_warnings": True,
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
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            progress = (downloaded / total * 100) if total else 0.0
            self._schedule(self._update_progress(download_id, progress))
        elif d["status"] == "finished":
            self._schedule(self._update_progress(download_id, 100.0))

    def _schedule(self, coro) -> None:
        if self._loop:
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _update_progress(self, download_id: int, progress: float) -> None:
        async with async_session() as session:
            dl = await session.get(Download, download_id)
            if dl:
                dl.progress = progress
                dl.status = "downloading"
                await session.commit()

    async def _on_success(self, download_id: int, info: dict, owner: str) -> None:
        async with async_session() as session:
            dl = await session.get(Download, download_id)
            if not dl:
                return

            items = [
                item
                for entry in _iter_downloaded_entries(info)
                if (item := self._build_media_item(entry, owner, download_id, dl.url))
            ]

            if not items:
                # ignoreerrors=True means a playlist/channel download where
                # every entry individually failed (e.g. rate-limited, 403s,
                # or a per-entry download that never got past a `.part` file)
                # still returns a truthy top-level `info` -- without this
                # check that gets recorded as "done" despite nothing actually
                # landing on disk.
                dl.status = "error"
                dl.error = "no files were successfully downloaded"
                dl.finished_at = datetime.utcnow()
                await session.commit()
                return

            dl.status = "done"
            dl.progress = 100.0
            dl.finished_at = datetime.utcnow()
            dl.title = info.get("title")
            dl.platform = info.get("extractor")

            for item in items:
                session.add(item)

            await session.commit()

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
            return None
        # Normalise away double slashes that arise when optional template
        # components (e.g. playlist) are absent and evaluate to "".
        abs_path = str(Path(abs_path))
        abs_path = _apply_episode_prefix(abs_path, entry)
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
                dl.status = "error"
                dl.error = error
                dl.finished_at = datetime.utcnow()
                await session.commit()


downloader = Downloader()


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
        await session.commit()
        rows = [(dl.id, dl.url, dl.owner) for dl in stuck]

    for download_id, url, owner in rows:
        downloader.enqueue(download_id, url, owner, force=True)
        logger.info("recovered interrupted download %d: %s", download_id, url)

    if rows:
        logger.info("re-enqueued %d interrupted download(s) after restart", len(rows))
