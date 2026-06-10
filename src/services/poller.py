import asyncio
import logging
from datetime import datetime

import yt_dlp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from db import Download, MediaItem, Source, async_session
from services.downloader import downloader

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def poll_source(source_id: int) -> None:
    async with async_session() as session:
        source = await session.get(Source, source_id)
        if not source or not source.enabled:
            return

        is_first_poll = source.last_polled_at is None
        logger.info("polling source %d (%s): %s", source_id, "first" if is_first_poll else "update", source.url)

        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: _extract_flat(source.url))
        except Exception as exc:
            logger.error("failed to fetch source %d: %s", source_id, exc)
            source.last_polled_at = datetime.utcnow()
            await session.commit()
            return

        if not source.label and info:
            source.label = info.get("title") or info.get("uploader")
            source.platform = info.get("extractor")

        entries = info.get("entries") if info else None
        urls = _collect_urls(info, entries)

        if is_first_poll:
            # First poll: record all existing URLs as already known so we only
            # download content that appears *after* the user started following.
            logger.info(
                "source %d: first poll found %d existing items — marking as seen, not downloading",
                source_id, len(urls),
            )
        else:
            for url in urls:
                already_dl = (
                    await session.execute(select(Download).where(Download.url == url).limit(1))
                ).scalar_one_or_none()
                if already_dl:
                    continue
                already_media = (
                    await session.execute(select(MediaItem).where(MediaItem.source_url == url).limit(1))
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


def _extract_flat(url: str) -> dict:
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
        return ydl.extract_info(url, download=False) or {}


def _collect_urls(info: dict, entries) -> list[str]:
    if entries:
        return [
            e.get("webpage_url") or e.get("url")
            for e in entries
            if e.get("webpage_url") or e.get("url")
        ]
    url = info.get("webpage_url") or info.get("url")
    return [url] if url else []


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
