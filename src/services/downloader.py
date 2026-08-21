import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from logging import getLogger
from os import environ
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp

from db import Download, MediaItem, async_session
from services.episode_naming import resolve_episode

logger = getLogger(__name__)

MEDIA_ROOT = environ.get("MEDIA_ROOT", "/app/downloads")
COOKIES_ROOT = environ.get("COOKIES_ROOT", "/app/cookies")
CONCURRENCY = int(environ.get("DOWNLOAD_CONCURRENCY", "2"))

# How many times a failed item gets automatically requeued before we give up
# on it for good, and how long to wait before each attempt -- long enough
# that a transient site-side issue (rate limiting, a PO Token hiccup) has a
# real chance to clear rather than immediately re-tripping the same limit.
_MAX_AUTO_RETRIES = 3
_RETRY_DELAYS_SECONDS = [120, 600, 1800]  # 2min, 10min, 30min


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
    """

    def __init__(self, download_id: int) -> None:
        self._download_id = download_id

    def debug(self, msg: str) -> None:
        # yt-dlp routes its normal status/progress chatter here too (as
        # "screen" output) when a logger is set -- keep it at debug so it's
        # opt-in via LOG_LEVEL rather than always-on noise.
        logger.debug("download %d: %s", self._download_id, msg)

    def warning(self, msg: str) -> None:
        logger.warning("download %d: %s", self._download_id, msg)

    def error(self, msg: str) -> None:
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


def _extract_flat_entries(url: str, owner: str) -> list[dict] | None:
    """Cheap, download-free listing of a collection's entries, used only to
    compute download order (see `_ordered_playlist_items`) before the real
    download starts. Returns None when yt-dlp reports no `entries` at all --
    i.e. `url` wasn't actually a collection, so callers should skip the
    reordering step entirely rather than treat an empty list as "0 items"."""
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
        info = ydl.extract_info(url, download=False) or {}
    return info.get("entries")


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


class Downloader:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=CONCURRENCY)
        self._loop: asyncio.AbstractEventLoop | None = None
        # download_id -> {original playlist_index: position in download order}.
        # Populated in _run before a collection download starts, read by
        # _progress_hook while it's in flight, and popped once it finishes --
        # see _ordered_playlist_items for why this exists.
        self._index_maps: dict[int, dict[int, int]] = {}

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def enqueue(self, download_id: int, url: str, owner: str = "anonymous", force: bool = False) -> None:
        self._executor.submit(self._run, download_id, url, owner, force)

    def _run(self, download_id: int, url: str, owner: str, force: bool = False) -> None:
        logger.info("download %d starting: %s", download_id, url)

        playlist_items = None
        if is_probably_collection_url(url):
            try:
                entries = _extract_flat_entries(url, owner)
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

        opts = self._build_opts(download_id, owner, force, playlist_items)
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
        finally:
            self._index_maps.pop(download_id, None)

    def _build_opts(
        self, download_id: int, owner: str, force: bool = False, playlist_items: str | None = None
    ) -> dict:
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
                "/%(playlist_title,playlist|)s"
                "/%(title)s.%(ext)s"
            ),
            "writeinfojson": True,
            "writethumbnail": True,
            "progress_hooks": [lambda d: self._progress_hook(d, download_id)],
            # Routes all of yt-dlp's own reporting (including what quiet/
            # no_warnings would otherwise suppress or print raw to stderr)
            # through our logger instead -- see _YtDlpLogAdapter.
            "logger": _YtDlpLogAdapter(download_id),
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
                await session.commit()
                self._schedule_retries(download_id, failed_entries, owner, source_id, retry_count)
                return

            dl.status = "done"
            dl.progress = 100.0
            dl.finished_at = datetime.utcnow()
            dl.title = info.get("title")
            dl.platform = info.get("extractor")
            dl.current_title = None

            for item in items:
                session.add(item)

            await session.commit()

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
                dl.current_title = None
                await session.commit()
                self._schedule_retries(
                    download_id,
                    [{"webpage_url": dl.url, "title": dl.title}],
                    dl.owner,
                    dl.source_id,
                    dl.retry_count,
                )

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
            asyncio.create_task(
                self._retry_after_delay(retry_url, owner, source_id, current_retry_count + 1, delay)
            )

    async def _retry_after_delay(
        self, url: str, owner: str, source_id: int | None, retry_count: int, delay: float
    ) -> None:
        await asyncio.sleep(delay)
        async with async_session() as session:
            dl = Download(url=url, owner=owner, source_id=source_id, retry_count=retry_count)
            session.add(dl)
            await session.commit()
            await session.refresh(dl)
        logger.info("download %d: auto-retry attempt %d for %s", dl.id, retry_count, url)
        self.enqueue(dl.id, url, owner)


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
            # force=True below discards any partial file, so a fresh run also
            # starts a fresh playlist position rather than showing stale counts.
            dl.current_index = None
            dl.total_entries = None
            dl.current_title = None
            dl.completed_items = None
        await session.commit()
        rows = [(dl.id, dl.url, dl.owner) for dl in stuck]

    for download_id, url, owner in rows:
        downloader.enqueue(download_id, url, owner, force=True)
        logger.info("recovered interrupted download %d: %s", download_id, url)

    if rows:
        logger.info("re-enqueued %d interrupted download(s) after restart", len(rows))
