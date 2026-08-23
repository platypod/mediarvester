"""services/downloader.py -- the auto-retry state machine.

Added after the 2026-08-20/21 incident: a failed item used to just... stay
failed. _compute_retry_at / _schedule_retries are what cap retries and
space them out so a transient site-side issue has a real chance to clear
before the next attempt.

The exact schedule was revised 2026-08-22: the original [120, 600, 1800]
(2/10/30 min) kept every retry inside the same hour YouTube's own
rate-limit message names ("for up to an hour"), so a real rate-limit trip
could burn every retry against the same still-active limit and give up
having never gotten past it (confirmed happening for real, MrDeriv's
VBssNWJl-bo). The last two tiers now reach well past that window.
"""

import asyncio

import pytest

from services.downloader import (
    _MAX_AUTO_RETRIES,
    _RETRY_DELAYS_SECONDS,
    Downloader,
)
from db import Download
from sqlalchemy import select


@pytest.fixture
def dl():
    return Downloader()


def test_compute_retry_at_is_none_when_cap_already_reached(dl):
    assert dl._compute_retry_at(_MAX_AUTO_RETRIES) is None
    assert dl._compute_retry_at(_MAX_AUTO_RETRIES + 1) is None


def test_compute_retry_at_returns_a_future_time_below_cap(dl):
    import datetime as dt

    before = dt.datetime.utcnow()
    retry_at = dl._compute_retry_at(0)
    assert retry_at is not None
    expected_min = before + dt.timedelta(seconds=_RETRY_DELAYS_SECONDS[0])
    assert retry_at >= expected_min


@pytest.mark.parametrize("retry_count", [0, 1, 2])
def test_compute_retry_at_uses_the_delay_for_this_attempt(dl, retry_count):
    import datetime as dt

    before = dt.datetime.utcnow()
    retry_at = dl._compute_retry_at(retry_count)
    delta = (retry_at - before).total_seconds()
    # Should be close to the configured delay for this attempt, not some
    # other attempt's delay.
    assert abs(delta - _RETRY_DELAYS_SECONDS[retry_count]) < 2


async def test_no_failed_entries_schedules_nothing(dl, monkeypatch):
    calls = []
    monkeypatch.setattr(dl, "_retry_after_delay", lambda *a, **kw: calls.append(a))
    dl._schedule_retries(1, [], "reivi", None, 0)
    await asyncio.sleep(0)
    assert calls == []


async def test_at_cap_schedules_nothing_and_gives_up(dl, monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(dl, "_retry_after_delay", lambda *a, **kw: calls.append(a))
    dl._schedule_retries(1, [{"webpage_url": "https://example.com/v"}], "reivi", None, _MAX_AUTO_RETRIES)
    await asyncio.sleep(0)
    assert calls == []


async def test_schedules_one_retry_per_failed_entry(dl, monkeypatch):
    scheduled = []

    async def fake_retry_after_delay(url, owner, source_id, retry_count, delay, title=None):
        scheduled.append((url, owner, source_id, retry_count, delay, title))

    monkeypatch.setattr(dl, "_retry_after_delay", fake_retry_after_delay)
    failed_entries = [
        {"webpage_url": "https://example.com/v1", "title": "Video 1"},
        {"webpage_url": "https://example.com/v2", "title": "Video 2"},
    ]
    dl._schedule_retries(1, failed_entries, "reivi", None, 0)
    await asyncio.sleep(0)

    assert len(scheduled) == 2
    urls = {s[0] for s in scheduled}
    assert urls == {"https://example.com/v1", "https://example.com/v2"}
    for _, owner, source_id, retry_count, delay, _ in scheduled:
        assert owner == "reivi"
        assert source_id is None
        assert retry_count == 1  # current_retry_count(0) + 1
        assert delay == _RETRY_DELAYS_SECONDS[0]


async def test_entries_with_no_url_are_skipped(dl, monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        dl, "_retry_after_delay", lambda *a, **kw: scheduled.append(a) or _noop_coro()
    )
    failed_entries = [{"title": "No URL here"}, {"url": "https://example.com/has-url"}]
    dl._schedule_retries(1, failed_entries, "reivi", None, 0)
    await asyncio.sleep(0)
    assert len(scheduled) == 1
    assert scheduled[0][0] == "https://example.com/has-url"


async def test_falls_back_to_plain_url_field_when_no_webpage_url(dl, monkeypatch):
    scheduled = []

    async def fake_retry_after_delay(url, owner, source_id, retry_count, delay, title=None):
        scheduled.append(url)

    monkeypatch.setattr(dl, "_retry_after_delay", fake_retry_after_delay)
    dl._schedule_retries(1, [{"url": "https://example.com/fallback"}], "reivi", None, 0)
    await asyncio.sleep(0)
    assert scheduled == ["https://example.com/fallback"]


async def _noop_coro():
    return None


async def test_retry_after_delay_supersedes_the_row_it_retries(dl, session, make_download, monkeypatch):
    # A failed row should stop showing as "error" once its own retry exists
    # -- an "error" filter in the UI should only ever surface the one
    # attempt that's still actually unresolved, not the whole retry history
    # (each retry is deliberately its own row -- see _schedule_retries).
    failed = await make_download(session, url="https://example.com/v1", status="error", retry_count=0)
    monkeypatch.setattr(dl, "enqueue", lambda *a, **kw: None)

    await dl._retry_after_delay("https://example.com/v1", "reivi", None, 1, 0, title="Some Video")

    await session.refresh(failed)
    assert failed.status == "retried"

    rows = (await session.execute(select(Download).where(Download.url == "https://example.com/v1"))).scalars().all()
    assert len(rows) == 2
    new_row = next(r for r in rows if r.id != failed.id)
    assert new_row.status != "retried"  # the new attempt itself must be untouched


async def test_retry_after_delay_does_not_touch_a_different_owners_error_row(
    dl, session, make_download, monkeypatch
):
    other_owner_row = await make_download(
        session, url="https://example.com/v1", owner="someone_else", status="error"
    )
    monkeypatch.setattr(dl, "enqueue", lambda *a, **kw: None)

    await dl._retry_after_delay("https://example.com/v1", "reivi", None, 1, 0)

    await session.refresh(other_owner_row)
    assert other_owner_row.status == "error"


def test_retry_schedule_reaches_well_past_youtubes_own_rate_limit_window():
    # A spec test, not a behavior test: locks in the actual numbers so a
    # future tweak has to consciously touch this assertion, rather than
    # silently drifting the schedule back inside the 1h rate-limit window
    # this was explicitly revised to escape (2026-08-22).
    assert _MAX_AUTO_RETRIES == 4
    assert _RETRY_DELAYS_SECONDS == [120, 1800, 3 * 3600, 24 * 3600]
    assert max(_RETRY_DELAYS_SECONDS) >= 24 * 3600
    assert sum(_RETRY_DELAYS_SECONDS[:2]) < 3600  # first couple stay quick
    assert _RETRY_DELAYS_SECONDS[-1] > 3600  # but the schedule does reach past 1h
