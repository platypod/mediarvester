import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import yt_dlp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select
from yt_dlp.utils import RejectedVideoReached

from db import Download, MediaItem, Source, async_session
from services.downloader import (
    downloader,
    get_cookies_path,
    is_probably_collection_url,
    matching_known_playlist,
    recover_missed_retries,
)
from services.service_status import compute_service_status
from services.telemetry import create_cached_gauge, meter, propagate_context, tracer

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

_polls = meter.create_counter("mediarvester.poller.poll", description="Source polls, by outcome")
_new_entries = meter.create_counter(
    "mediarvester.poller.new_entries", description="New entries discovered across all polls"
)

# Every source's discovery scan already paces its own requests
# (sleep_interval_requests=1 in _new_entries_in_playlist) -- but nothing
# stopped two sources' scans from running *concurrently*, which multiplies
# the effective request rate to YouTube by however many happen to fire at
# once. Sources on the same poll_interval_minutes land in lockstep forever
# (APScheduler's interval trigger counts from whenever the job was added,
# so sources added around the same time keep firing together on every
# cycle) -- confirmed happening for real 2026-08-22: 3 sources polled
# within the same second, YouTube rate-limited within 30s. This serializes
# every source's network-heavy work process-wide; a second source's poll
# simply waits its turn instead of racing the first one to YouTube.
_youtube_scan_lock = asyncio.Lock()


async def poll_source(source_id: int) -> None:
    with tracer.start_as_current_span("poll_source") as span:
        span.set_attribute("source_id", source_id)
        await _poll_source(source_id, span)


async def _poll_source(source_id: int, span) -> None:
    async with async_session() as session:
        source = await session.get(Source, source_id)
        if not source or not source.enabled:
            return
        span.set_attribute("owner", source.owner)

        logger.info("polling source %d: %s", source_id, source.url)

        cutoff_ts = source.created_at.replace(tzinfo=timezone.utc).timestamp()
        cookies = get_cookies_path(source.owner)
        async with _youtube_scan_lock:
            if not source.label:
                await _label_source(source)

            try:
                loop = asyncio.get_event_loop()
                entries, poll_error = await loop.run_in_executor(
                    None,
                    propagate_context(
                        lambda: _new_entries_since(source.url, source.include_shorts, cutoff_ts, cookies)
                    ),
                )
            except Exception as exc:
                logger.error("failed to fetch source %d: %s", source_id, exc)
                source.last_polled_at = datetime.utcnow()
                source.last_poll_error = str(exc)
                await session.commit()
                _polls.add(1, {"result": "error"})
                return

        if poll_error:
            logger.warning("source %d poll degraded: %s", source_id, poll_error)
        source.last_poll_error = poll_error
        _polls.add(1, {"result": "degraded" if poll_error else "ok"})
        _new_entries.add(len(entries))

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

            folder_hint = await matching_known_playlist(source.owner, entry)

            dl = Download(url=url, source_id=source_id, owner=source.owner, folder_hint=folder_hint)
            session.add(dl)
            await session.flush()
            downloader.enqueue(dl.id, url, source.owner, folder_hint=folder_hint)
            logger.info("enqueued download %d for %s", dl.id, url)

        source.last_polled_at = datetime.utcnow()
        await session.commit()


async def _label_source(source: Source) -> None:
    loop = asyncio.get_event_loop()
    cookies = get_cookies_path(source.owner)
    try:
        info = await loop.run_in_executor(None, propagate_context(lambda: _extract_flat(source.url, cookies)))
    except Exception as exc:
        logger.debug("could not label source %d: %s", source.id, exc)
        return
    if info:
        source.label = info.get("title") or info.get("uploader")
        source.platform = info.get("extractor")


# YouTube's auto-selected clients (e.g. android_vr) increasingly serve
# SABR-only streams or trip rate limits more readily than mweb does --
# see services/downloader.py's _build_opts for the full story and the
# 2026-08-19 incident that verified this empirically for downloads. Applied
# here too since discovery scans hit the same extractor.
_YOUTUBE_OPTS = {
    "js_runtimes": {"node": {}},
    "extractor_args": {"youtube": {"player_client": ["mweb"]}},
}


def _extract_flat(url: str, cookies: str | None) -> dict:
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, **_YOUTUBE_OPTS}
    if cookies:
        opts["cookiefile"] = cookies
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


_RELEVANT_TAB_SUFFIXES = ("/videos", "/streams", "/shorts")


def _new_entries_since(
    url: str, include_shorts: bool, cutoff_ts: float, cookies: str | None
) -> tuple[list[dict], str | None]:
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

    Returns `(entries, error)` -- `error` is set whenever a tab's scan was cut
    short by errors rather than cleanly reaching the cutoff. A source that
    degrades identically on every poll (e.g. stale cookies) would otherwise
    silently report "zero new videos" forever with no distinguishable signal
    from "genuinely nothing new" -- confirmed 2026-08-19, six weeks of missed
    uploads on a followed source with no error anywhere queryable.
    """
    tab_urls = _relevant_tab_urls(url, include_shorts, cookies)
    collected: list[dict] = []
    errors: list[str] = []
    for tab_url in tab_urls:
        try:
            entries, error = _new_entries_in_playlist(tab_url, cutoff_ts, cookies)
            collected.extend(entries)
            if error:
                errors.append(f"{tab_url}: {error}")
        except Exception as exc:
            logger.error("failed to scan %s: %s", tab_url, exc)
            errors.append(f"{tab_url}: {exc}")
    return collected, "; ".join(errors) if errors else None


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


def _remember(collected: dict[str, dict], info: dict) -> None:
    """Record an entry we intend to enqueue, keyed by video id.

    The filter fires more than once for the same video (yt-dlp's flat probe,
    then again once full metadata is in), so keying by id both dedupes the
    entry and lets the later, richer copy replace the earlier stub.
    """
    key = info.get("id") or info.get("webpage_url") or info.get("url") or ""
    collected[key] = info


def _build_match_filter(cutoff_ts: float, collected: dict[str, dict]):
    """Build the `match_filter` for a discovery scan: collect every entry
    published at/after `cutoff_ts` into `collected`, and reject the first one
    older than that so `break_on_reject` can abort the walk.

    NEVER gate this on `incomplete` being falsy. yt-dlp does not pass a falsy
    `incomplete` at all during a `download=False` scan -- its only
    `incomplete=False` call site is `process_info()`, i.e. the real download
    path. The "final" call a scan does get (`process_video_result`) passes
    `incomplete=YoutubeDL._format_fields`: a non-empty *set* ("complete except
    for these format fields"), which is truthy.

    An `if incomplete: return None` guard therefore matched every single call.
    Nothing was ever collected, and because no rejection string was ever
    returned, `break_on_reject` never fired either -- so every poll silently
    re-walked the channel's entire back catalog (holding all of it in memory,
    see `extract_flat` in YoutubeDL's __process_playlist_result) while
    reporting "0 new entries, no error". Confirmed in Mimir before it was
    found: `mediarvester_poller_new_entries` flat at 0 across ~300 consecutive
    `result="ok"` polls. Judge on whether the date is actually present instead.

    `incomplete is True` is the one reliable marker of yt-dlp's *preliminary*
    probe (the flat playlist-entry stage); the set and `False` both mean "this
    is your last chance to judge this entry". The regression test asserts that
    contract against the real yt-dlp, so a change upstream fails loudly.
    """

    def match_filter(info: dict, *, incomplete: bool | set[str] = False) -> str | None:
        published = info.get("timestamp")
        if published is None:
            published = _parse_upload_date(info.get("upload_date"))
        if published is None:
            if incomplete is True:
                # Preliminary probe -- the full fetch may still supply a date,
                # so stay undecided rather than collecting a dateless stub.
                return None
            # Last call and still no date (extractor doesn't expose one) --
            # let it through and rely on the Download/MediaItem dedupe check
            # to avoid re-fetching.
            _remember(collected, info)
            return None
        if published >= cutoff_ts:
            _remember(collected, info)
            return None
        return "published before the follow date"

    return match_filter


class _ScanLogger:
    """Captures yt-dlp's own error output for one discovery scan.

    `ignoreerrors=True` is required here (one gated video mustn't blind the
    whole scan), but it also means yt-dlp's `trouble()` records an internal
    retcode instead of raising -- so `except DownloadError` around
    `extract_info` is unreachable, and a scan aborted by
    `skip_playlist_after_errors` still returned "no error". Routing yt-dlp's
    logging here is the only way those failures become visible.

    yt-dlp's contract (YoutubeDL.to_stderr / report_warning / to_screen):
    `report_error` -> logger.error, `report_warning` -> logger.warning,
    ordinary screen output -> logger.debug. Only `error` counts as a failure;
    setting a logger also bypasses the `no_warnings` filter, so warnings are
    logged for humans but deliberately not treated as a degraded scan.
    """

    # What yt-dlp reports when skip_playlist_after_errors trips and it gives
    # up on the rest of the playlist. Matching it is what separates "the scan
    # aborted" from "one gated video was skipped". A test asserts this string
    # still appears in yt-dlp's source, so a reword upstream fails loudly
    # rather than silently downgrading every abort to "tolerated".
    _ABORT_MARKER = "Skipping the remaining entries"

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.aborted = False

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        logger.debug("yt-dlp warning while scanning: %s", msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        if self._ABORT_MARKER in msg:
            self.aborted = True
        logger.warning("yt-dlp error while scanning: %s", msg)

    def failure(self, *, found_entries: bool) -> str | None:
        """The scan's failure as one `last_poll_error` string, or None.

        Individual entry failures are tolerated by design -- a stray
        age-restricted or private video mustn't blind the scan, let alone flag
        the whole source as degraded -- so on their own they only get logged.

        Report when the scan actually gave up (`_ABORT_MARKER`), or when errors
        left it with nothing at all to show. That second case is what a
        systemic failure looks like (stale cookies, every request rejected),
        and it is exactly the "silently reports zero new videos forever"
        scenario the error signal exists for -- see `_new_entries_since`.
        """
        if not self.errors:
            return None
        if not self.aborted and found_entries:
            logger.info(
                "tolerated %d entry error(s) while scanning; entries still found",
                len(self.errors),
            )
            return None
        head = "; ".join(self.errors[:3])
        extra = len(self.errors) - 3
        return head if extra <= 0 else f"{head} (+{extra} more)"


def _new_entries_in_playlist(url: str, cutoff_ts: float, cookies: str | None) -> tuple[list[dict], str | None]:
    """Fetch full per-video metadata one entry at a time (newest first),
    stopping as soon as an entry older than `cutoff_ts` is hit. This is only
    cheap because `break_on_reject` aborts extraction the moment it reaches
    content that predates the follow -- it never walks the whole catalog.

    Returns `(entries, error)` -- see `_new_entries_since` for why the error
    signal matters as much as the entries themselves.
    """
    collected: dict[str, dict] = {}
    scan_log = _ScanLogger()

    opts = {
        "quiet": True,
        "no_warnings": True,
        # "Always process, but don't return the result from inside a playlist"
        # (yt-dlp's own CLI default). Entries are still fully resolved -- the
        # resolution gate in process_ie_result only short-circuits for
        # 'in_playlist'/True -- so match_filter still sees full metadata, but
        # yt-dlp stops accumulating every resolved entry in memory for the
        # lifetime of the scan. With False it kept them all: a scan of a large
        # back catalog held full metadata for every video at once, which is a
        # strong suspect for the memory growth in
        # docs/incidents/2026-08-20-youtube-403-and-oom.md.
        "extract_flat": "discard_in_playlist",
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
        "match_filter": _build_match_filter(cutoff_ts, collected),
        # Fetching full per-video metadata for every entry back-to-back with no
        # delay is what trips YouTube's rate limiter in the first place (the
        # skip_playlist_after_errors guard above only bounds the damage after
        # the fact). Spacing requests out keeps the scan under the threshold.
        "sleep_interval_requests": 1,
        "logger": scan_log,
        **_YOUTUBE_OPTS,
    }
    if cookies:
        opts["cookiefile"] = cookies
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=False)
    except RejectedVideoReached:
        # Reached content older than the follow date -- the scan's clean stop.
        # Errors logged before that point still count, so fall through.
        pass
    except yt_dlp.utils.DownloadError as exc:
        # Only reachable if ignoreerrors is ever turned off above; while it's
        # on, trouble() swallows these and _ScanLogger is what sees them.
        logger.warning("stopped scanning %s early after an error: %s", url, exc)
        return list(collected.values()), str(exc)
    return list(collected.values()), scan_log.failure(found_entries=bool(collected))


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


_set_degraded_gauge = create_cached_gauge(
    "mediarvester.service.degraded", "1 if downloads look site-wide degraded (see service_status.py), else 0"
)
_set_media_items_total_gauge = create_cached_gauge(
    "mediarvester.media_items.total", "Total MediaItem rows across all owners"
)


async def _refresh_gauges() -> None:
    """Both of these need an async DB query, which an ObservableGauge
    callback can't do (must be synchronous) -- so this just runs on the
    same interval as everything else here and pushes fresh values into the
    cached gauges (services/telemetry.py's create_cached_gauge)."""
    async with async_session() as session:
        status = await compute_service_status(session)
        _set_degraded_gauge(1 if status["degraded"] else 0)
        total = (await session.execute(select(func.count()).select_from(MediaItem))).scalar_one()
        _set_media_items_total_gauge(total)


async def init_scheduler() -> None:
    async with async_session() as session:
        result = await session.execute(select(Source).where(Source.enabled == True))
        for source in result.scalars().all():
            # Don't run_now on startup — sources already have last_polled_at set
            schedule_source(source, run_now=False)
    scheduler.add_job(_refresh_gauges, "interval", seconds=60, id="refresh_gauges", next_run_time=datetime.now(timezone.utc))
    # recover_missed_retries also runs once at startup (main.py's lifespan) --
    # that alone only catches a retry_at that already elapsed by the moment
    # the process comes up. A redeploy landing *before* retry_at but after
    # the previous process (and its in-memory asyncio sleep task) is gone
    # gets missed by both: the old task is dead, and the new process's
    # one-shot check still sees retry_at in the future. Nothing ever
    # revisited it again after that -- confirmed happening for real
    # (owner=reivi, 2026-08-22: two auto-retries stuck for 23+ hours with
    # no error, no log, nothing). Running this on an interval closes that
    # gap; it's a cheap no-op query when there's nothing to recover.
    scheduler.add_job(recover_missed_retries, "interval", minutes=5, id="recover_missed_retries")
    scheduler.start()
    logger.info("scheduler started")
