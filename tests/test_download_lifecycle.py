"""services/downloader.py -- Downloader._on_success / _on_error / _build_media_item.

The state machine that turns a yt-dlp result into DB state: single video
vs. collection, partial-success handling, the "ignoreerrors still returns a
truthy info dict" trap that used to record empty playlists as done, the
creator fallback (2026-08-21 fix -- collections never had it populated,
silently breaking playlist-folder matching), and the stale-error-row
cleanup on success (2026-08-21 feature).
"""

import pytest

import services.downloader as downloader_module
from services.downloader import Downloader


@pytest.fixture(autouse=True)
def _skip_real_file_verification(monkeypatch):
    # This file is about the on_success/on_error DB state machine, not file
    # integrity -- the test videos below are placeholder bytes, not real
    # media, so real ffprobe verification would reject every one of them.
    # See test_file_verification.py for _verify_downloaded_file's own tests.
    monkeypatch.setattr(downloader_module, "_verify_downloaded_file", lambda *a, **kw: None)


def _write(path, content: bytes = b"fake video bytes"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


async def test_single_video_success_creates_one_media_item(session, make_download, tmp_path, monkeypatch):
    monkeypatch.setattr(downloader_module, "MEDIA_ROOT", str(tmp_path))
    dl = await make_download(session, url="https://www.youtube.com/watch?v=abc123")
    video_path = tmp_path / "MrDeriv" / "Some Video.mp4"
    _write(video_path)

    info = {
        "title": "Some Video",
        "extractor": "youtube",
        "uploader": "MrDeriv",
        "requested_downloads": [{"filepath": str(video_path)}],
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "duration": 120,
    }
    downloader = Downloader()
    await downloader._on_success(dl.id, info, "reivi")

    await session.refresh(dl)
    assert dl.status == "done"
    assert dl.progress == 100.0
    assert dl.creator == "MrDeriv"
    assert dl.platform == "youtube"

    from db import MediaItem
    from sqlalchemy import select

    items = (await session.execute(select(MediaItem).where(MediaItem.download_id == dl.id))).scalars().all()
    assert len(items) == 1
    assert items[0].title == "Some Video"
    assert items[0].owner == "reivi"


async def test_collection_with_some_failed_entries_still_succeeds(
    session, make_download, tmp_path, monkeypatch
):
    monkeypatch.setattr(downloader_module, "MEDIA_ROOT", str(tmp_path))
    dl = await make_download(session, url="https://youtube.com/playlist?list=PL1")
    ok_path = tmp_path / "MrDeriv" / "Playlist" / "Episode 1.mp4"
    _write(ok_path)

    info = {
        "title": "Playlist",
        "entries": [
            {
                "title": "Episode 1",
                "extractor": "youtube",
                "uploader": "MrDeriv",
                "requested_downloads": [{"filepath": str(ok_path)}],
                "webpage_url": "https://www.youtube.com/watch?v=ep1",
            },
            {
                # No requested_downloads at all -- extraction failed entirely
                # for this entry (still non-None thanks to ignoreerrors).
                "title": "Episode 2",
                "webpage_url": "https://www.youtube.com/watch?v=ep2",
            },
        ],
    }
    downloader = Downloader()
    # One entry failed -- avoid actually scheduling a real background retry
    # task (network call, 2min sleep) that would outlive this test.
    monkeypatch.setattr(downloader, "_schedule_retries", lambda *a, **kw: None)
    await downloader._on_success(dl.id, info, "reivi")

    await session.refresh(dl)
    assert dl.status == "done"

    from db import MediaItem
    from sqlalchemy import select

    items = (await session.execute(select(MediaItem).where(MediaItem.download_id == dl.id))).scalars().all()
    assert len(items) == 1
    assert items[0].title == "Episode 1"


async def test_collection_creator_falls_back_to_first_entry_when_top_level_is_empty(
    session, make_download, tmp_path, monkeypatch
):
    # The real bug found 2026-08-21: a playlist/channel-tab's top-level info
    # dict routinely has no uploader/channel/creator at all (those are
    # per-entry fields) -- every collection Download row ended up with
    # creator=None, so it could never itself serve as a matching_known_playlist
    # candidate later. Confirm the fallback to the first entry's own field.
    monkeypatch.setattr(downloader_module, "MEDIA_ROOT", str(tmp_path))
    dl = await make_download(session, url="https://youtube.com/playlist?list=PL1")
    ok_path = tmp_path / "MrDeriv" / "Playlist" / "Episode 1.mp4"
    _write(ok_path)

    info = {
        "title": "Playlist",
        # No uploader/channel/creator at this level.
        "entries": [
            {
                "title": "Episode 1",
                "uploader": "MrDeriv",
                "requested_downloads": [{"filepath": str(ok_path)}],
                "webpage_url": "https://www.youtube.com/watch?v=ep1",
            },
        ],
    }
    downloader = Downloader()
    await downloader._on_success(dl.id, info, "reivi")

    await session.refresh(dl)
    assert dl.creator == "MrDeriv"


async def test_collection_with_every_entry_failed_is_recorded_as_error_not_done(
    session, make_download, tmp_path, monkeypatch
):
    # ignoreerrors=True means yt-dlp still returns a truthy top-level info
    # dict even when every entry failed -- without this check that used to
    # get recorded as "done" with zero files on disk.
    monkeypatch.setattr(downloader_module, "MEDIA_ROOT", str(tmp_path))
    dl = await make_download(session, url="https://youtube.com/playlist?list=PL1")
    info = {
        "title": "Playlist",
        "entries": [
            {"title": "Episode 1", "webpage_url": "https://www.youtube.com/watch?v=ep1"},
            {"title": "Episode 2", "webpage_url": "https://www.youtube.com/watch?v=ep2"},
        ],
    }
    downloader = Downloader()
    monkeypatch.setattr(downloader, "_schedule_retries", lambda *a, **kw: None)
    await downloader._on_success(dl.id, info, "reivi")

    await session.refresh(dl)
    assert dl.status == "error"
    assert dl.error == "no files were successfully downloaded"
    assert dl.retry_at is not None  # a retry should have been scheduled


async def test_a_requested_download_path_that_does_not_exist_on_disk_is_not_a_success(
    session, make_download, tmp_path, monkeypatch
):
    # requested_downloads can be present (format selection succeeded) with
    # no real file behind it -- e.g. a .part left stuck mid-transfer. Only
    # an existing final path counts.
    monkeypatch.setattr(downloader_module, "MEDIA_ROOT", str(tmp_path))
    dl = await make_download(session, url="https://www.youtube.com/watch?v=abc123")
    info = {
        "title": "Some Video",
        "requested_downloads": [{"filepath": str(tmp_path / "MrDeriv" / "Some Video.mp4")}],
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
    }
    downloader = Downloader()
    monkeypatch.setattr(downloader, "_schedule_retries", lambda *a, **kw: None)
    await downloader._on_success(dl.id, info, "reivi")

    await session.refresh(dl)
    assert dl.status == "error"


async def test_thumbnail_is_detected_when_present_alongside_the_video(
    session, make_download, tmp_path, monkeypatch
):
    monkeypatch.setattr(downloader_module, "MEDIA_ROOT", str(tmp_path))
    dl = await make_download(session, url="https://www.youtube.com/watch?v=abc123")
    video_path = tmp_path / "MrDeriv" / "Some Video.mp4"
    _write(video_path)
    _write(tmp_path / "MrDeriv" / "Some Video.webp", b"fake image")

    info = {
        "title": "Some Video",
        "requested_downloads": [{"filepath": str(video_path)}],
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
    }
    downloader = Downloader()
    await downloader._on_success(dl.id, info, "reivi")

    from db import MediaItem
    from sqlalchemy import select

    item = (await session.execute(select(MediaItem).where(MediaItem.download_id == dl.id))).scalar_one()
    assert item.thumbnail_path == "MrDeriv/Some Video.webp"


async def test_success_clears_stale_error_rows_for_the_same_url_and_owner(
    session, make_download, tmp_path, monkeypatch
):
    # 2026-08-21 feature: a resubmit that succeeds means earlier failed
    # attempts at the same URL are no longer telling the truth.
    monkeypatch.setattr(downloader_module, "MEDIA_ROOT", str(tmp_path))
    url = "https://www.youtube.com/watch?v=abc123"
    stale1 = await make_download(session, url=url, status="error", owner="reivi")
    stale2 = await make_download(session, url=url, status="error", owner="reivi")
    other_owner_error = await make_download(session, url=url, status="error", owner="someone_else")
    dl = await make_download(session, url=url, owner="reivi")

    video_path = tmp_path / "MrDeriv" / "Some Video.mp4"
    _write(video_path)
    info = {
        "title": "Some Video",
        "requested_downloads": [{"filepath": str(video_path)}],
        "webpage_url": url,
    }
    downloader = Downloader()
    await downloader._on_success(dl.id, info, "reivi")

    from db import Download
    from sqlalchemy import select

    remaining_ids = {
        row.id for row in (await session.execute(select(Download.id))).all()
    }
    assert stale1.id not in remaining_ids
    assert stale2.id not in remaining_ids
    assert dl.id in remaining_ids
    # A different owner's error row for the same URL must survive --
    # libraries are per-owner.
    assert other_owner_error.id in remaining_ids


async def test_success_does_not_touch_other_urls_error_rows(session, make_download, tmp_path, monkeypatch):
    monkeypatch.setattr(downloader_module, "MEDIA_ROOT", str(tmp_path))
    unrelated = await make_download(
        session, url="https://www.youtube.com/watch?v=different", status="error", owner="reivi"
    )
    dl = await make_download(session, url="https://www.youtube.com/watch?v=abc123", owner="reivi")
    video_path = tmp_path / "MrDeriv" / "Some Video.mp4"
    _write(video_path)
    info = {
        "title": "Some Video",
        "requested_downloads": [{"filepath": str(video_path)}],
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
    }
    downloader = Downloader()
    await downloader._on_success(dl.id, info, "reivi")

    from db import Download

    still_there = await session.get(Download, unrelated.id)
    assert still_there is not None


async def test_on_error_carries_over_a_known_title_to_the_retry_entry(session, make_download, monkeypatch):
    # dl.title is only ever set on success, but current_title may already be
    # known from a progress event before the eventual failure -- worth
    # keeping for the retry row rather than falling back to a bare URL.
    dl = await make_download(
        session,
        url="https://www.youtube.com/watch?v=abc123",
        current_title="Some Video (in progress)",
    )
    downloader = Downloader()
    captured = {}

    def fake_schedule_retries(download_id, failed_entries, owner, source_id, retry_count):
        captured["failed_entries"] = failed_entries

    monkeypatch.setattr(downloader, "_schedule_retries", fake_schedule_retries)
    await downloader._on_error(dl.id, "some error")

    await session.refresh(dl)
    assert dl.status == "error"
    assert dl.error == "some error"
    assert captured["failed_entries"] == [
        {"webpage_url": dl.url, "title": "Some Video (in progress)"}
    ]


async def test_on_error_sets_retry_at_when_below_cap(session, make_download, monkeypatch):
    dl = await make_download(session, url="https://www.youtube.com/watch?v=abc123", retry_count=0)
    downloader = Downloader()
    monkeypatch.setattr(downloader, "_schedule_retries", lambda *a, **kw: None)
    await downloader._on_error(dl.id, "some error")
    await session.refresh(dl)
    assert dl.retry_at is not None


async def test_on_error_leaves_retry_at_none_when_cap_reached(session, make_download):
    from services.downloader import _MAX_AUTO_RETRIES

    dl = await make_download(
        session, url="https://www.youtube.com/watch?v=abc123", retry_count=_MAX_AUTO_RETRIES
    )
    downloader = Downloader()
    await downloader._on_error(dl.id, "some error")
    await session.refresh(dl)
    assert dl.retry_at is None


def test_log_adapter_remembers_the_last_error_for_the_generic_failure_message():
    # 2026-08-22 fix: ignoreerrors=True means extract_info() returning
    # falsy has no exception to inspect -- the *real* reason (rate-limited,
    # 403, a specific format unavailable...) only ever existed as a log
    # line via this adapter. Without capturing it here, both the final log
    # line and the DB's error column (what the UI actually shows) end up
    # with nothing more specific than "no media extracted for <url>".
    from services.downloader import _YtDlpLogAdapter

    adapter = _YtDlpLogAdapter(download_id=1)
    assert adapter.last_error is None
    adapter.warning("Requested format is not available")
    assert adapter.last_error == "Requested format is not available"
    adapter.error("[youtube] abc123: rate-limited by YouTube for up to an hour")
    assert adapter.last_error == "[youtube] abc123: rate-limited by YouTube for up to an hour"


def test_log_adapter_debug_messages_do_not_count_as_the_last_error():
    from services.downloader import _YtDlpLogAdapter

    adapter = _YtDlpLogAdapter(download_id=1)
    adapter.debug("[youtube] Extracting URL: https://example.com")
    assert adapter.last_error is None
