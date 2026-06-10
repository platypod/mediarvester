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

    def enqueue(self, download_id: int, url: str, owner: str = "anonymous") -> None:
        self._executor.submit(self._run, download_id, url, owner)

    def _run(self, download_id: int, url: str, owner: str) -> None:
        opts = self._build_opts(download_id, owner)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            self._schedule(self._on_success(download_id, info or {}, owner))
        except Exception as exc:
            logger.error("download %d failed: %s", download_id, exc)
            self._schedule(self._on_error(download_id, str(exc)))

    def _build_opts(self, download_id: int, owner: str) -> dict:
        opts: dict = {
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
