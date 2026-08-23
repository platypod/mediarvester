"""services/downloader.py -- matching_known_playlist.

Resolves a `folder_hint` so a video that belongs to an already-downloaded
playlist joins it in the library instead of landing loose in the creator's
flat root folder. Two real bugs lived here (found 2026-08-21 while cleaning
up a library mess): every completed collection's Download.creator was
None (so it could never be a candidate at all), and this only ever ran
inside the poller's own pipeline, never for a manually (re)submitted URL.
Both are covered here so they can't quietly regress.
"""

from datetime import datetime

import services.downloader as downloader_module
from services.downloader import matching_known_playlist


async def test_no_match_without_a_video_id():
    assert await matching_known_playlist("reivi", {"uploader": "MrDeriv"}) is None


async def test_no_match_without_a_creator():
    assert await matching_known_playlist("reivi", {"id": "abc123"}) is None


async def test_no_candidates_when_no_completed_collection_exists(session, make_download):
    await make_download(session, url="https://example.com/single-video", status="done", creator="MrDeriv")
    result = await matching_known_playlist("reivi", {"id": "abc123", "uploader": "MrDeriv"})
    assert result is None


async def test_incomplete_collection_is_not_a_candidate(session, make_download):
    await make_download(
        session,
        url="https://youtube.com/playlist?list=PL1",
        status="downloading",
        creator="MrDeriv",
        title="Elden Ring avec Deriv",
    )
    result = await matching_known_playlist("reivi", {"id": "abc123", "uploader": "MrDeriv"})
    assert result is None


async def test_a_non_collection_url_is_not_a_candidate_even_if_done(session, make_download):
    # A single completed video download from the same creator isn't a
    # playlist to place other videos alongside.
    await make_download(
        session,
        url="https://www.youtube.com/watch?v=abc123",
        status="done",
        creator="MrDeriv",
        title="Some Video",
    )
    result = await matching_known_playlist("reivi", {"id": "xyz789", "uploader": "MrDeriv"})
    assert result is None


async def test_matches_when_video_id_is_a_member_of_a_known_playlist(session, make_download, monkeypatch):
    await make_download(
        session,
        url="https://youtube.com/playlist?list=PL1",
        status="done",
        creator="MrDeriv",
        title="Elden Ring avec Deriv",
    )
    monkeypatch.setattr(
        downloader_module,
        "extract_flat_entries",
        lambda url, owner: [{"id": "other"}, {"id": "abc123"}],
    )
    result = await matching_known_playlist("reivi", {"id": "abc123", "uploader": "MrDeriv"})
    assert result == "Elden Ring avec Deriv"


async def test_no_match_when_video_id_is_not_a_member(session, make_download, monkeypatch):
    await make_download(
        session,
        url="https://youtube.com/playlist?list=PL1",
        status="done",
        creator="MrDeriv",
        title="Elden Ring avec Deriv",
    )
    monkeypatch.setattr(
        downloader_module, "extract_flat_entries", lambda url, owner: [{"id": "unrelated"}]
    )
    result = await matching_known_playlist("reivi", {"id": "abc123", "uploader": "MrDeriv"})
    assert result is None


async def test_stops_at_first_matching_playlist(session, make_download, monkeypatch):
    # Two known playlists from the same creator -- only the first one that
    # actually contains the video should be returned, and membership checks
    # for playlists after the match shouldn't even be attempted.
    await make_download(
        session,
        url="https://youtube.com/playlist?list=PL_NEWER",
        status="done",
        creator="MrDeriv",
        title="Newer Playlist",
        finished_at=datetime(2026, 8, 21, 10, 0, 0),
    )
    await make_download(
        session,
        url="https://youtube.com/playlist?list=PL_OLDER",
        status="done",
        creator="MrDeriv",
        title="Older Playlist",
        finished_at=datetime(2026, 8, 20, 10, 0, 0),
    )
    checked_urls = []

    def fake_extract(url, owner):
        checked_urls.append(url)
        return [{"id": "abc123"}]  # every playlist "contains" it

    monkeypatch.setattr(downloader_module, "extract_flat_entries", fake_extract)
    result = await matching_known_playlist("reivi", {"id": "abc123", "uploader": "MrDeriv"})
    assert result == "Newer Playlist"
    assert checked_urls == ["https://youtube.com/playlist?list=PL_NEWER"]


async def test_a_playlist_lookup_error_is_tolerated_and_the_next_candidate_is_tried(
    session, make_download, monkeypatch
):
    await make_download(
        session,
        url="https://youtube.com/playlist?list=PL_BROKEN",
        status="done",
        creator="MrDeriv",
        title="Broken Playlist",
        finished_at=datetime(2026, 8, 21, 10, 0, 0),
    )
    await make_download(
        session,
        url="https://youtube.com/playlist?list=PL_GOOD",
        status="done",
        creator="MrDeriv",
        title="Good Playlist",
        finished_at=datetime(2026, 8, 20, 10, 0, 0),
    )

    def fake_extract(url, owner):
        if "BROKEN" in url:
            raise RuntimeError("network error")
        return [{"id": "abc123"}]

    monkeypatch.setattr(downloader_module, "extract_flat_entries", fake_extract)
    result = await matching_known_playlist("reivi", {"id": "abc123", "uploader": "MrDeriv"})
    assert result == "Good Playlist"


async def test_entry_creator_resolves_via_channel_field_fallback(session, make_download, monkeypatch):
    # entry has no "uploader", only "channel" -- same fallback chain as the
    # outtmpl folder-name resolution should apply here too.
    await make_download(
        session,
        url="https://youtube.com/playlist?list=PL1",
        status="done",
        creator="MrDeriv",
        title="Elden Ring avec Deriv",
    )
    monkeypatch.setattr(downloader_module, "extract_flat_entries", lambda url, owner: [{"id": "abc123"}])
    result = await matching_known_playlist("reivi", {"id": "abc123", "channel": "MrDeriv"})
    assert result == "Elden Ring avec Deriv"


async def test_playlist_row_still_matches_after_many_newer_episode_downloads(
    session, make_download, monkeypatch
):
    # 2026-08-23 bug: the candidate query used to cap at the 20
    # most-recently-finished `done` rows for the creator *before* filtering
    # down to actual collection URLs -- a playlist's own marker row only
    # gets one finished_at (when the collection itself was downloaded), so
    # once 20+ individual episodes from that creator finished afterward, the
    # marker row silently fell out of the window and every later video from
    # that creator landed loose in the flat root folder again, with no
    # error anywhere. Confirmed happening for real (MrDeriv, both "Elden
    # Ring" and "Kingdom Come..." went stale this way).
    await make_download(
        session,
        url="https://youtube.com/playlist?list=PL1",
        status="done",
        creator="MrDeriv",
        title="Elden Ring",
        finished_at=datetime(2026, 8, 22, 8, 58, 0),
    )
    for i in range(25):
        await make_download(
            session,
            url=f"https://example.com/episode-{i}",
            status="done",
            creator="MrDeriv",
            title=f"Episode {i}",
            finished_at=datetime(2026, 8, 22, 9, 0, 0),
        )
    monkeypatch.setattr(downloader_module, "extract_flat_entries", lambda url, owner: [{"id": "abc123"}])
    result = await matching_known_playlist("reivi", {"id": "abc123", "uploader": "MrDeriv"})
    assert result == "Elden Ring"


async def test_matching_is_scoped_to_the_requesting_owner(session, make_download, monkeypatch):
    # Another owner's completed playlist from the same creator name must not
    # leak a folder_hint into a different owner's download -- each owner's
    # library is independent (see CLAUDE.md's "owner" field on every model).
    await make_download(
        session,
        url="https://youtube.com/playlist?list=PL1",
        status="done",
        creator="MrDeriv",
        title="Someone Else's Playlist",
        owner="someone_else",
    )
    monkeypatch.setattr(downloader_module, "extract_flat_entries", lambda url, owner: [{"id": "abc123"}])
    result = await matching_known_playlist("reivi", {"id": "abc123", "uploader": "MrDeriv"})
    assert result is None
