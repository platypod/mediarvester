"""services/playlists.py -- local playlist ownership.

A followed playlist (or a playlist downloaded in full) is recorded locally
with its member video ids, so a video fetched on its own -- by a channel
source, a manual URL, an auto-retry -- is still filed into the show it
belongs to. Regression coverage for "new episodes of a watched playlist land
loose in singles/": the old folder_hint only changed yt-dlp's output folder
and library_layout.place() ignored it.
"""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

import services.downloader as downloader_module
import services.playlists as playlists
from db import MediaItem, Playlist
from services import library_layout
from services.downloader import Downloader

PL_URL = "https://www.youtube.com/playlist?list=PLkoh"


@pytest.fixture(autouse=True)
def _skip_real_file_verification(monkeypatch):
    monkeypatch.setattr(downloader_module, "_verify_downloaded_file", lambda *a, **kw: None)


def _write(path, content: bytes = b"fake video bytes"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


async def _koh_lanta(**kw):
    defaults = dict(
        owner="reivi", playlist_id="PLkoh", title="Koh-Lanta replay", url=PL_URL,
        creator="Domingo", member_ids=["vid1", "vid2"], followed=True,
    )
    defaults.update(kw)
    return await playlists.record_playlist(**defaults)


# --- url helpers ---------------------------------------------------------

def test_playlist_url_detection():
    assert playlists.is_playlist_url(PL_URL)
    assert playlists.is_playlist_url("https://www.youtube.com/watch?v=x&list=PLabc")
    assert not playlists.is_playlist_url("https://www.youtube.com/@Domingo")
    assert not playlists.is_playlist_url("https://www.youtube.com/watch?v=x")


def test_video_id_from_url():
    assert playlists.video_id_of("https://www.youtube.com/watch?v=abc123&t=3") == "abc123"
    assert playlists.video_id_of("https://youtu.be/abc123") == "abc123"
    assert playlists.video_id_of("https://example.com/video") is None


# --- layout: ownership alone makes an episode a series episode ----------

def test_owned_unnumbered_video_is_a_series_even_without_an_existing_show(tmp_path):
    entry = {"id": "v", "title": "Replay du dimanche", "uploader": "Domingo",
             "playlist_title": "Koh-Lanta replay"}
    assert library_layout.classify(entry, str(tmp_path)) == "singles"  # status quo
    owned = {**entry, library_layout.OWNED_KEY: True}
    assert library_layout.classify(owned, str(tmp_path)) == "series"
    directory, stem = library_layout.target_for(owned, str(tmp_path), ".mp4")
    assert directory.endswith("series/Domingo - Koh-Lanta replay/Season 01")
    assert stem == "Domingo - Koh-Lanta replay - S01E01 - Replay du dimanche"


# --- registry ------------------------------------------------------------

async def test_dump_playlists_are_never_recorded(session):
    assert await _koh_lanta(title="Vidéos") is None
    assert (await session.execute(select(Playlist))).scalars().all() == []


async def test_record_playlist_replaces_members_and_is_idempotent(session):
    await _koh_lanta()
    pl = await _koh_lanta(member_ids=["vid2", "vid3"])
    assert await playlists.owning_playlist("reivi", "vid1") is None
    assert (await playlists.owning_playlist("reivi", "vid3")).id == pl.id
    assert len((await session.execute(select(Playlist))).scalars().all()) == 1


async def test_ownership_is_per_owner(session):
    await _koh_lanta()
    assert await playlists.owning_playlist("someone-else", "vid1") is None


async def test_followed_playlist_wins_and_first_match_is_stable(session):
    older = await _koh_lanta(playlist_id="PLa", title="Older", followed=False, member_ids=["vid1"])
    await _koh_lanta(playlist_id="PLb", title="Newer", followed=False, member_ids=["vid1"])
    assert (await playlists.owning_playlist("reivi", "vid1")).id == older.id
    followed = await _koh_lanta(playlist_id="PLc", title="Followed", followed=True, member_ids=["vid1"])
    assert (await playlists.owning_playlist("reivi", "vid1")).id == followed.id


# --- resolve: lazy refresh of stale followed playlists -------------------

async def test_resolve_refreshes_a_stale_followed_playlist_and_stops_at_first_match(session, monkeypatch):
    stale = await _koh_lanta(member_ids=["old"])
    other = await _koh_lanta(playlist_id="PLother", title="Other", member_ids=[])
    async with playlists.async_session() as s:  # age both snapshots
        for pk in (stale.id, other.id):
            (await s.get(Playlist, pk)).members_refreshed_at = datetime.utcnow() - timedelta(hours=2)
        await s.commit()

    fetched = []

    def fake_fetch(url, owner):
        fetched.append(url)
        return {"id": "PLkoh", "title": "Koh-Lanta replay", "uploader": "Domingo",
                "entries": [{"id": "old"}, {"id": "brand-new"}]}

    monkeypatch.setattr(playlists, "fetch_playlist", fake_fetch)
    got = await playlists.resolve_owner("reivi", {"id": "brand-new", "uploader": "Domingo"})
    assert got.id == stale.id
    assert fetched == [PL_URL]  # the second stale playlist was never fetched


async def test_resolve_does_not_refetch_a_fresh_snapshot(session, monkeypatch):
    await _koh_lanta()
    monkeypatch.setattr(
        playlists, "fetch_playlist", lambda *a: pytest.fail("fresh snapshot must not be refetched")
    )
    assert await playlists.resolve_owner("reivi", {"id": "unrelated", "uploader": "Domingo"}) is None
    assert (await playlists.resolve_owner("reivi", {"id": "vid1", "uploader": "Domingo"})) is not None


async def test_resolve_survives_a_failing_fetch(session, monkeypatch):
    pl = await _koh_lanta()
    async with playlists.async_session() as s:
        (await s.get(Playlist, pl.id)).members_refreshed_at = datetime.utcnow() - timedelta(days=1)
        await s.commit()

    def boom(*a):
        raise RuntimeError("429")

    monkeypatch.setattr(playlists, "fetch_playlist", boom)
    assert await playlists.resolve_owner("reivi", {"id": "zzz", "uploader": "Domingo"}) is None


# --- placement on download success --------------------------------------

async def test_single_video_of_a_followed_playlist_is_filed_into_its_show(
    session, make_download, tmp_path, monkeypatch
):
    monkeypatch.setattr(downloader_module, "MEDIA_ROOT", str(tmp_path))
    await _koh_lanta()
    # No folder_hint on the download: retries and manual URLs never had one.
    dl = await make_download(session, url="https://www.youtube.com/watch?v=vid1")
    video = tmp_path / "Domingo" / "Replay du dimanche.mp4"
    _write(video)
    info = {
        "id": "vid1", "title": "Replay du dimanche", "extractor": "youtube", "uploader": "Domingo",
        "requested_downloads": [{"filepath": str(video)}],
        "webpage_url": "https://www.youtube.com/watch?v=vid1",
    }
    await Downloader()._on_success(dl.id, info, "reivi")

    expected = (
        tmp_path / "series" / "Domingo - Koh-Lanta replay" / "Season 01"
        / "Domingo - Koh-Lanta replay - S01E01 - Replay du dimanche.mp4"
    )
    assert expected.exists()
    item = (await session.execute(select(MediaItem).where(MediaItem.download_id == dl.id))).scalar_one()
    assert item.local_path == str(expected.relative_to(tmp_path))
    assert item.video_id == "vid1"
    assert item.playlist_pk is not None


async def test_next_episode_continues_after_what_is_on_disk(session, make_download, tmp_path, monkeypatch):
    monkeypatch.setattr(downloader_module, "MEDIA_ROOT", str(tmp_path))
    await _koh_lanta()
    season = tmp_path / "series" / "Domingo - Koh-Lanta replay" / "Season 01"
    _write(season / "Domingo - Koh-Lanta replay - S01E07 - Hand moved.mp4")
    dl = await make_download(session, url="https://www.youtube.com/watch?v=vid2")
    video = tmp_path / "Domingo" / "Suite.mp4"
    _write(video)
    info = {"id": "vid2", "title": "Suite", "uploader": "Domingo",
            "requested_downloads": [{"filepath": str(video)}],
            "webpage_url": "https://www.youtube.com/watch?v=vid2"}
    await Downloader()._on_success(dl.id, info, "reivi")
    assert (season / "Domingo - Koh-Lanta replay - S01E08 - Suite.mp4").exists()


async def test_video_outside_any_playlist_still_goes_to_singles(session, make_download, tmp_path, monkeypatch):
    monkeypatch.setattr(downloader_module, "MEDIA_ROOT", str(tmp_path))
    await _koh_lanta()
    dl = await make_download(session, url="https://www.youtube.com/watch?v=other")
    video = tmp_path / "Domingo" / "Alone.mp4"
    _write(video)
    info = {"id": "other", "title": "Alone", "uploader": "Domingo",
            "requested_downloads": [{"filepath": str(video)}],
            "webpage_url": "https://www.youtube.com/watch?v=other"}
    await Downloader()._on_success(dl.id, info, "reivi")
    assert (tmp_path / "singles" / "Domingo" / "Alone.mp4").exists()


async def test_finished_playlist_download_registers_the_playlist(session, make_download, tmp_path, monkeypatch):
    monkeypatch.setattr(downloader_module, "MEDIA_ROOT", str(tmp_path))
    dl = await make_download(session, url=PL_URL)
    video = tmp_path / "Domingo" / "Koh-Lanta replay" / "Ep 1.mp4"
    _write(video)
    info = {
        "id": "PLkoh", "title": "Koh-Lanta replay", "uploader": "Domingo",
        "entries": [{
            "id": "vid1", "title": "Ep 1", "uploader": "Domingo", "playlist_title": "Koh-Lanta replay",
            "playlist_index": 1, "requested_downloads": [{"filepath": str(video)}],
            "webpage_url": "https://www.youtube.com/watch?v=vid1",
        }, {"id": "vid2", "title": "Ep 2", "uploader": "Domingo"}],
    }
    d = Downloader()
    d._schedule_retries = lambda *a, **kw: None
    await d._on_success(dl.id, info, "reivi")
    pl = await playlists.owning_playlist("reivi", "vid2")
    assert pl is not None and pl.title == "Koh-Lanta replay" and not pl.followed


# --- late addition: reconcile a video that arrived before its playlist ----

async def _loose_item(session, make_download, make_media_item, tmp_path, *, created_at, video_id="vid1"):
    dl = await make_download(session, url=f"https://www.youtube.com/watch?v={video_id}", status="done")
    folder = tmp_path / "singles" / "Domingo"
    _write(folder / "Replay.mp4")
    _write(folder / "Replay-poster.jpg", b"x" * 2000)
    (folder / "Replay.nfo").write_text("<movie/>")
    (folder / "Replay.info.json").write_text(
        json.dumps({"id": video_id, "title": "Replay", "uploader": "Domingo"})
    )
    return await make_media_item(
        session, dl.id, title="Replay", local_path="singles/Domingo/Replay.mp4",
        thumbnail_path="singles/Domingo/Replay-poster.jpg",
        source_url=f"https://www.youtube.com/watch?v={video_id}", video_id=video_id,
        created_at=created_at,
    )


async def test_reconcile_moves_a_loose_video_into_the_show_that_claims_it(
    session, make_download, make_media_item, tmp_path
):
    pl = await _koh_lanta()
    item = await _loose_item(session, make_download, make_media_item, tmp_path,
                             created_at=datetime.utcnow() + timedelta(minutes=1))
    report = await playlists.reconcile_playlist(pl, str(tmp_path))

    season = tmp_path / "series" / "Domingo - Koh-Lanta replay" / "Season 01"
    stem = "Domingo - Koh-Lanta replay - S01E01 - Replay"
    assert (season / f"{stem}.mp4").exists()
    assert (season / f"{stem}-thumb.jpg").exists()
    assert (season / f"{stem}.nfo").read_text().startswith("<?xml") and "<episodedetails>" in (season / f"{stem}.nfo").read_text()
    assert (season / f"{stem}.info.json").exists()
    assert not (tmp_path / "singles" / "Domingo" / "Replay.mp4").exists()
    assert not (tmp_path / "singles" / "Domingo" / "Replay.nfo").exists()
    assert report["moved"] == [item.id]

    await session.refresh(item)
    assert item.local_path == f"series/Domingo - Koh-Lanta replay/Season 01/{stem}.mp4"
    assert item.thumbnail_path == f"series/Domingo - Koh-Lanta replay/Season 01/{stem}-thumb.jpg"
    assert item.playlist_pk == pl.id


async def test_reconcile_only_reports_items_older_than_the_playlist(
    session, make_download, make_media_item, tmp_path
):
    # The singles/ backfill is report-only until the owner opts in.
    pl = await _koh_lanta()
    item = await _loose_item(session, make_download, make_media_item, tmp_path,
                             created_at=datetime(2020, 1, 1))
    report = await playlists.reconcile_playlist(pl, str(tmp_path))
    assert report["moved"] == []
    assert [r["media_id"] for r in report["would_move"]] == [item.id]
    assert (tmp_path / "singles" / "Domingo" / "Replay.mp4").exists()
    await session.refresh(item)
    assert item.local_path == "singles/Domingo/Replay.mp4"


async def test_reconcile_never_moves_a_file_that_already_sits_in_a_show(
    session, make_download, make_media_item, tmp_path
):
    pl = await _koh_lanta()
    dl = await make_download(session, status="done")
    show_file = tmp_path / "series" / "Other show" / "Season 01" / "Other show - S01E01 - Replay.mp4"
    _write(show_file)
    item = await make_media_item(
        session, dl.id, local_path=str(show_file.relative_to(tmp_path)), video_id="vid1",
        created_at=datetime.utcnow() + timedelta(minutes=1),
    )
    report = await playlists.reconcile_playlist(pl, str(tmp_path))
    assert report == {"moved": [], "would_move": []}
    assert show_file.exists()
    await session.refresh(item)
    assert item.playlist_pk is None


async def test_reconcile_takes_the_next_free_number_when_the_slot_is_occupied(
    session, make_download, make_media_item, tmp_path
):
    pl = await _koh_lanta()
    item = await _loose_item(session, make_download, make_media_item, tmp_path,
                             created_at=datetime.utcnow() + timedelta(minutes=1))
    season = tmp_path / "series" / "Domingo - Koh-Lanta replay" / "Season 01"
    occupant = season / "Domingo - Koh-Lanta replay - S01E01 - Hand moved.mp4"
    _write(occupant)
    report = await playlists.reconcile_playlist(pl, str(tmp_path))
    assert report["moved"] == [item.id]
    assert occupant.exists()
    assert (season / "Domingo - Koh-Lanta replay - S01E02 - Replay.mp4").exists()


async def test_backfill_preview_lists_loose_items_without_moving_them(
    session, make_download, make_media_item, tmp_path
):
    await _koh_lanta()
    item = await _loose_item(session, make_download, make_media_item, tmp_path,
                             created_at=datetime(2020, 1, 1))
    preview = await playlists.preview_backfill("reivi", str(tmp_path))
    assert [(p["media_id"], p["playlist"]) for p in preview] == [(item.id, "Koh-Lanta replay")]
    assert (tmp_path / "singles" / "Domingo" / "Replay.mp4").exists()


# --- poller integration ---------------------------------------------------

import services.poller as poller_module  # noqa: E402
from services.poller import _poll_source  # noqa: E402


class _Span:
    def set_attribute(self, *a, **kw):
        pass


async def _noop(*a, **kw):
    return None


def _fetch_returning(*ids):
    return lambda url, owner: {
        "id": "PLkoh", "title": "Koh-Lanta replay", "uploader": "Domingo",
        "entries": [{"id": i} for i in ids],
    }


async def test_polling_a_playlist_source_records_it_and_files_a_video_the_channel_got_first(
    session, make_source, make_download, make_media_item, tmp_path, monkeypatch
):
    monkeypatch.setattr(downloader_module, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(poller_module, "_label_source", _noop)
    monkeypatch.setattr(poller_module, "_new_entries_since", lambda *a, **kw: ([], None))
    monkeypatch.setattr(playlists, "fetch_playlist", _fetch_returning("vid1"))
    source = await make_source(session, url=PL_URL)
    item = await _loose_item(session, make_download, make_media_item, tmp_path,
                             created_at=datetime.utcnow() + timedelta(minutes=5))

    await _poll_source(source.id, _Span())

    pl = await playlists.owning_playlist("reivi", "vid1")
    assert pl is not None and pl.followed and pl.source_id == source.id
    await session.refresh(item)
    assert item.local_path.startswith("series/Domingo - Koh-Lanta replay/Season 01/")
    assert not (tmp_path / "singles" / "Domingo" / "Replay.mp4").exists()


async def test_a_failing_playlist_fetch_does_not_break_the_poll(session, make_source, monkeypatch):
    monkeypatch.setattr(poller_module, "_label_source", _noop)
    monkeypatch.setattr(poller_module, "_new_entries_since", lambda *a, **kw: ([], None))

    def boom(*a):
        raise RuntimeError("429")

    monkeypatch.setattr(playlists, "fetch_playlist", boom)
    source = await make_source(session, url=PL_URL)
    await _poll_source(source.id, _Span())
    await session.refresh(source)
    assert source.last_polled_at is not None


async def test_a_channel_source_never_touches_the_playlist_registry(session, make_source, monkeypatch):
    monkeypatch.setattr(poller_module, "_label_source", _noop)
    monkeypatch.setattr(poller_module, "_new_entries_since", lambda *a, **kw: ([], None))
    monkeypatch.setattr(playlists, "fetch_playlist", lambda *a: pytest.fail("not a playlist source"))
    source = await make_source(session, url="https://www.youtube.com/@Domingo")
    await _poll_source(source.id, _Span())
