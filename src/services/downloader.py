import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from logging import getLogger
from os import environ
from pathlib import Path

import yt_dlp

from db import Download, MediaItem, async_session

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
            self._schedule(self._on_success(download_id, info or {}, owner))
        except Exception as exc:
            logger.error("download %d failed: %s", download_id, exc)
            self._schedule(self._on_error(download_id, str(exc)))

    def _build_opts(self, download_id: int, owner: str, force: bool = False) -> dict:
        opts: dict = {
            # Use Node.js for YouTube's n-challenge (requires yt-dlp-ejs + Node 22+).
            # yt-dlp defaults to deno-only; node must be explicitly enabled.
            "js_runtimes": {"node": {}},
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

            dl.status = "done"
            dl.progress = 100.0
            dl.finished_at = datetime.utcnow()
            dl.title = info.get("title")
            dl.platform = info.get("extractor")

            requested = info.get("requested_downloads") or [{}]
            abs_path = requested[0].get("filepath", "")
            # Normalise away double slashes that arise when optional template
            # components (e.g. playlist) are absent and evaluate to "".
            if abs_path:
                abs_path = str(Path(abs_path))
            local_path = os.path.relpath(abs_path, MEDIA_ROOT) if abs_path else ""

            thumbnail_path: str | None = None
            if local_path:
                base = Path(MEDIA_ROOT) / local_path
                for ext in (".jpg", ".png", ".webp"):
                    candidate = base.with_suffix(ext)
                    if candidate.exists():
                        thumbnail_path = os.path.relpath(str(candidate), MEDIA_ROOT)
                        break

            item = MediaItem(
                title=info.get("title") or dl.url,
                platform=info.get("extractor"),
                source_url=info.get("webpage_url") or dl.url,
                local_path=local_path,
                thumbnail_path=thumbnail_path,
                duration_seconds=info.get("duration"),
                owner=owner,
                download_id=download_id,
            )
            session.add(item)
            await session.commit()

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
