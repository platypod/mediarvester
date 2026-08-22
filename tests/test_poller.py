"""services/poller.py -- discovery dedup, tab resolution, and upload-date parsing.

Real yt-dlp network calls are mocked out throughout -- these test the
surrounding logic (what counts as "already have it", which tabs get
scanned, how a degraded scan is surfaced) rather than yt-dlp itself.
"""

from datetime import datetime

import services.poller as poller_module
from services.poller import _parse_upload_date, _relevant_tab_urls, _poll_source


def test_parse_upload_date_valid():
    ts = _parse_upload_date("20260821")
    assert ts is not None
    dt = datetime.utcfromtimestamp(ts)
    assert (dt.year, dt.month, dt.day) == (2026, 8, 21)


def test_parse_upload_date_none_when_absent():
    assert _parse_upload_date(None) is None


def test_parse_upload_date_empty_string():
    assert _parse_upload_date("") is None


class _FakeSpan:
    def set_attribute(self, *a, **kw):
        pass


def test_relevant_tab_urls_resolves_channel_tabs(monkeypatch):
    monkeypatch.setattr(
        poller_module,
        "_extract_flat",
        lambda url, cookies: {
            "entries": [
                {"_type": "playlist", "webpage_url": "https://www.youtube.com/@Creator/videos"},
                {"_type": "playlist", "webpage_url": "https://www.youtube.com/@Creator/shorts"},
                {"_type": "playlist", "webpage_url": "https://www.youtube.com/@Creator/streams"},
                {"_type": "playlist", "webpage_url": "https://www.youtube.com/@Creator/playlists"},
            ]
        },
    )
    urls = _relevant_tab_urls("https://www.youtube.com/@Creator", include_shorts=False, cookies=None)
    assert urls == [
        "https://www.youtube.com/@Creator/videos",
        "https://www.youtube.com/@Creator/streams",
    ]


def test_relevant_tab_urls_includes_shorts_when_asked(monkeypatch):
    monkeypatch.setattr(
        poller_module,
        "_extract_flat",
        lambda url, cookies: {
            "entries": [
                {"_type": "playlist", "webpage_url": "https://www.youtube.com/@Creator/videos"},
                {"_type": "playlist", "webpage_url": "https://www.youtube.com/@Creator/shorts"},
            ]
        },
    )
    urls = _relevant_tab_urls("https://www.youtube.com/@Creator", include_shorts=True, cookies=None)
    assert "https://www.youtube.com/@Creator/shorts" in urls


def test_relevant_tab_urls_falls_back_to_original_url_when_not_a_channel(monkeypatch):
    # A direct playlist URL flat-extracts to actual videos, not nested
    # "playlist"-type tabs -- should be returned as-is, not resolved further.
    monkeypatch.setattr(
        poller_module,
        "_extract_flat",
        lambda url, cookies: {"entries": [{"_type": "video", "id": "abc123"}]},
    )
    urls = _relevant_tab_urls("https://youtube.com/playlist?list=PL1", include_shorts=False, cookies=None)
    assert urls == ["https://youtube.com/playlist?list=PL1"]


async def test_poll_source_skips_entries_already_in_download_table(
    session, make_source, make_download, monkeypatch
):
    source = await make_source(session, url="https://www.youtube.com/@Creator")
    await make_download(
        session, url="https://www.youtube.com/watch?v=already", owner=source.owner, status="done"
    )

    monkeypatch.setattr(poller_module, "_label_source", _async_noop)
    monkeypatch.setattr(
        poller_module,
        "_new_entries_since",
        lambda *a, **kw: (
            [{"webpage_url": "https://www.youtube.com/watch?v=already", "id": "already"}],
            None,
        ),
    )
    enqueued = []
    monkeypatch.setattr(poller_module.downloader, "enqueue", lambda *a, **kw: enqueued.append(a))
    monkeypatch.setattr(poller_module, "matching_known_playlist", _async_none)

    await _poll_source(source.id, _FakeSpan())

    assert enqueued == []


async def test_poll_source_skips_entries_already_in_media_item_table(
    session, make_source, make_media_item, monkeypatch
):
    source = await make_source(session, url="https://www.youtube.com/@Creator")
    # A MediaItem can exist without a matching Download row in some paths
    # (e.g. historical data) -- the media_item check is a second, independent
    # guard against re-downloading something already in the library.
    from db import Download

    dl = Download(url="https://example.com/placeholder", owner=source.owner, status="done")
    session.add(dl)
    await session.commit()
    await session.refresh(dl)
    await make_media_item(
        session, dl.id, source_url="https://www.youtube.com/watch?v=already", owner=source.owner
    )

    monkeypatch.setattr(poller_module, "_label_source", _async_noop)
    monkeypatch.setattr(
        poller_module,
        "_new_entries_since",
        lambda *a, **kw: (
            [{"webpage_url": "https://www.youtube.com/watch?v=already", "id": "already"}],
            None,
        ),
    )
    enqueued = []
    monkeypatch.setattr(poller_module.downloader, "enqueue", lambda *a, **kw: enqueued.append(a))
    monkeypatch.setattr(poller_module, "matching_known_playlist", _async_none)

    await _poll_source(source.id, _FakeSpan())

    assert enqueued == []


async def test_poll_source_enqueues_genuinely_new_entries_with_folder_hint(
    session, make_source, monkeypatch
):
    source = await make_source(session, url="https://www.youtube.com/@Creator")

    monkeypatch.setattr(poller_module, "_label_source", _async_noop)
    monkeypatch.setattr(
        poller_module,
        "_new_entries_since",
        lambda *a, **kw: (
            [{"webpage_url": "https://www.youtube.com/watch?v=new1", "id": "new1", "uploader": "Creator"}],
            None,
        ),
    )
    enqueued = []
    monkeypatch.setattr(poller_module.downloader, "enqueue", lambda *a, **kw: enqueued.append((a, kw)))

    async def fake_matching(owner, entry):
        return "Known Playlist"

    monkeypatch.setattr(poller_module, "matching_known_playlist", fake_matching)

    await _poll_source(source.id, _FakeSpan())

    assert len(enqueued) == 1
    args, kwargs = enqueued[0]
    assert args[1] == "https://www.youtube.com/watch?v=new1"
    assert kwargs.get("folder_hint") == "Known Playlist"


async def test_poll_source_records_last_poll_error_on_exception(session, make_source, monkeypatch):
    source = await make_source(session, url="https://www.youtube.com/@Creator")
    monkeypatch.setattr(poller_module, "_label_source", _async_noop)

    def raise_error(*a, **kw):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(poller_module, "_new_entries_since", raise_error)

    await _poll_source(source.id, _FakeSpan())

    await session.refresh(source)
    assert source.last_poll_error == "network exploded"


async def test_poll_source_records_degraded_error_without_raising(session, make_source, monkeypatch):
    source = await make_source(session, url="https://www.youtube.com/@Creator")
    monkeypatch.setattr(poller_module, "_label_source", _async_noop)
    monkeypatch.setattr(
        poller_module, "_new_entries_since", lambda *a, **kw: ([], "some tabs failed to scan")
    )

    await _poll_source(source.id, _FakeSpan())

    await session.refresh(source)
    assert source.last_poll_error == "some tabs failed to scan"
    assert source.last_polled_at is not None


async def test_poll_source_disabled_source_is_a_noop(session, make_source, monkeypatch):
    source = await make_source(session, url="https://www.youtube.com/@Creator", enabled=False)
    called = []
    monkeypatch.setattr(poller_module, "_new_entries_since", lambda *a, **kw: called.append(1))
    await _poll_source(source.id, _FakeSpan())
    assert called == []


async def _async_noop(*a, **kw):
    return None


async def _async_none(*a, **kw):
    return None


async def test_concurrent_polls_never_scan_youtube_at_the_same_time(
    session, make_source, monkeypatch
):
    # 2026-08-22 fix: sources on the same poll_interval_minutes land in
    # lockstep forever (APScheduler's interval trigger counts from whenever
    # the job was added), and nothing stopped their scans running
    # concurrently -- confirmed happening for real: 3 sources polled within
    # the same second, YouTube rate-limited within 30s. _youtube_scan_lock
    # should mean only one source's scan is ever actually in flight at once,
    # no matter how many poll_source calls overlap.
    source_a = await make_source(session, url="https://www.youtube.com/@A")
    source_b = await make_source(session, url="https://www.youtube.com/@B")
    monkeypatch.setattr(poller_module, "_label_source", _async_noop)
    monkeypatch.setattr(poller_module.downloader, "enqueue", lambda *a, **kw: None)

    concurrent_scans = 0
    max_concurrent_scans = 0

    def fake_new_entries_since(*a, **kw):
        nonlocal concurrent_scans, max_concurrent_scans
        concurrent_scans += 1
        max_concurrent_scans = max(max_concurrent_scans, concurrent_scans)
        try:
            # Simulate a slow scan -- long enough that, without the lock,
            # the two polls below would clearly overlap.
            import time

            time.sleep(0.05)
            return [], None
        finally:
            concurrent_scans -= 1

    monkeypatch.setattr(poller_module, "_new_entries_since", fake_new_entries_since)

    import asyncio

    await asyncio.gather(
        _poll_source(source_a.id, _FakeSpan()),
        _poll_source(source_b.id, _FakeSpan()),
    )

    assert max_concurrent_scans == 1
