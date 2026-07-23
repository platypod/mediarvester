import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import yt_dlp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from yt_dlp.utils import RejectedVideoReached

from db import Download, MediaItem, Source, async_session
from services.downloader import downloader, get_cookies_path

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def poll_source(source_id: int) -> None:
    async with async_session() as session:
        source = await session.get(Source, source_id)
        if not source or not source.enabled:
            return

        logger.info("polling source %d: %s", source_id, source.url)

        if not source.label:
            await _label_source(source)

        cutoff_ts = source.created_at.replace(tzinfo=timezone.utc).timestamp()
        cookies = get_cookies_path(source.owner)
        try:
            loop = asyncio.get_event_loop()
            entries = await loop.run_in_executor(
                None, lambda: _new_entries_since(source.url, source.include_shorts, cutoff_ts, cookies)
            )
        except Exception as exc:
            logger.error("failed to fetch source %d: %s", source_id, exc)
            source.last_polled_at = datetime.utcnow()
            await session.commit()
            return

        for entry in entries:
            url = entry.get("webpage_url") or entry.get("url")
            if not url:
                continue
            already_dl = (
                await session.execute(
                    select(Download)
                    .where(Download.owner == source.owner)
                    .where(Download.url == url)
                    .where(Download.status.in_(("queued", "downloading", "done")))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if already_dl:
                continue
            already_media = (
                await session.execute(
                    select(MediaItem)
                    .where(MediaItem.owner == source.owner)
                    .where(MediaItem.source_url == url)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if already_media:
                continue

            dl = Download(url=url, source_id=source_id, owner=source.owner)
            session.add(dl)
            await session.flush()
            downloader.enqueue(dl.id, url, source.owner)
            logger.info("enqueued download %d for %s", dl.id, url)

        source.last_polled_at = datetime.utcnow()
        await session.commit()


async def _label_source(source: Source) -> None:
    loop = asyncio.get_event_loop()
    cookies = get_cookies_path(source.owner)
    try:
        info = await loop.run_in_executor(None, lambda: _extract_flat(source.url, cookies))
    except Exception:
        return
    if info:
        source.label = info.get("title") or info.get("uploader")
        source.platform = info.get("extractor")


def _extract_flat(url: str, cookies: str | None) -> dict:
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
    if cookies:
        opts["cookiefile"] = cookies
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


_RELEVANT_TAB_SUFFIXES = ("/videos", "/streams", "/shorts")


def _new_entries_since(url: str, include_shorts: bool, cutoff_ts: float, cookies: str | None) -> list[dict]:
    """Only ever return videos published at/after `cutoff_ts` (the follow date),
    regardless of what has or hasn't been recorded as already downloaded. This is
    the hard ceiling: even if the Download/MediaItem bookkeeping is wrong, empty,
    or reset, the channel's full back catalog can never be mistaken for "new".

    A bare channel URL (e.g. .../@handle) flat-extracts to its tabs
    (Videos/Shorts/Live) rather than individual videos -- each tab is itself a
    nested playlist. Resolve to the relevant tab(s) first so a break in one
    doesn't swallow the others.

    The owner's cookies are applied here, same as in downloader.py: a
    private/membership-only or age-restricted video that the account actually
    has access to would otherwise look like an unrecoverable error during
    discovery, even though downloading it would have succeeded.
    """
    tab_urls = _relevant_tab_urls(url, include_shorts, cookies)
    collected: list[dict] = []
    for tab_url in tab_urls:
        try:
            collected.extend(_new_entries_in_playlist(tab_url, cutoff_ts, cookies))
        except Exception as exc:
            logger.error("failed to scan %s: %s", tab_url, exc)
    return collected


def _relevant_tab_urls(url: str, include_shorts: bool, cookies: str | None) -> list[str]:
    info = _extract_flat(url, cookies)
    entries = info.get("entries")
    if not entries or not all(e.get("_type") == "playlist" for e in entries):
        return [url]

    wanted_suffixes = tuple(
        suffix for suffix in _RELEVANT_TAB_SUFFIXES if include_shorts or suffix != "/shorts"
    )
    tab_urls: list[str] = []
    for tab in entries:
        tab_url = tab.get("webpage_url") or tab.get("url") or ""
        path = urlparse(tab_url).path.rstrip("/").lower()
        if any(path.endswith(suffix) for suffix in wanted_suffixes):
            tab_urls.append(tab_url)
    return tab_urls or [url]


def _new_entries_in_playlist(url: str, cutoff_ts: float, cookies: str | None) -> list[dict]:
    """Fetch full per-video metadata one entry at a time (newest first),
    stopping as soon as an entry older than `cutoff_ts` is hit. This is only
    cheap because `break_on_reject` aborts extraction the moment it reaches
    content that predates the follow -- it never walks the whole catalog.
    """
    collected: list[dict] = []

    def match_filter(info: dict, *, incomplete: bool = False) -> str | None:
        if incomplete:
            # yt-dlp probes with partial info before the full per-video fetch;
            # judge (and collect) only once full metadata is available.
            return None
        published = info.get("timestamp")
        if published is None:
            published = _parse_upload_date(info.get("upload_date"))
        if published is None:
            # Can't judge age for this extractor/entry -- let it through and
            # rely on the Download/MediaItem dedupe check to avoid re-fetching.
            collected.append(info)
            return None
        if published >= cutoff_ts:
            collected.append(info)
            return None
        return "published before the follow date"

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "lazy_playlist": True,
        "break_on_reject": True,
        # A stray age-restricted/private video shouldn't blind the scan to
        # everything after it, so individual failures are tolerated (same
        # reasoning as downloader.py). But blanket ignoreerrors alone caused
        # the rate-limit incident: a systemic failure (every entry erroring)
        # never trips break_on_reject and silently walks the entire catalog.
        # skip_playlist_after_errors bounds that -- a few gated videos scroll
        # past, but a run of failures aborts the scan instead of tunneling
        # through history.
        "ignoreerrors": True,
        "skip_playlist_after_errors": 3,
        "match_filter": match_filter,
        # Fetching full per-video metadata for every entry back-to-back with no
        # delay is what trips YouTube's rate limiter in the first place (the
        # skip_playlist_after_errors guard above only bounds the damage after
        # the fact). Spacing requests out keeps the scan under the threshold.
        "sleep_interval_requests": 1,
    }
    if cookies:
        opts["cookiefile"] = cookies
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=False)
    except RejectedVideoReached:
        pass
    except yt_dlp.utils.DownloadError as exc:
        logger.warning("stopped scanning %s early after an error: %s", url, exc)
    return collected


def _parse_upload_date(date: str | None) -> float | None:
    if not date:
        return None
    return datetime.strptime(date, "%Y%m%d").replace(tzinfo=timezone.utc).timestamp()


def schedule_source(source: Source, run_now: bool = False) -> None:
    from datetime import timezone
    job_id = f"source_{source.id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    if source.enabled:
        kwargs = {}
        if run_now:
            # Fire immediately, then on the regular interval
            kwargs["next_run_time"] = datetime.now(timezone.utc)
        scheduler.add_job(
            poll_source,
            "interval",
            minutes=source.poll_interval_minutes,
            args=[source.id],
            id=job_id,
            **kwargs,
        )


async def init_scheduler() -> None:
    async with async_session() as session:
        result = await session.execute(select(Source).where(Source.enabled == True))
        for source in result.scalars().all():
            # Don't run_now on startup — sources already have last_polled_at set
            schedule_source(source, run_now=False)
    scheduler.start()
    logger.info("scheduler started")
