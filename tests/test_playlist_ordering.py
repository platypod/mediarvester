"""services/downloader.py -- _ordered_playlist_items.

Added after the 2026-08-20/21 incident: a "newest first" playlist would
otherwise download the finale before episode 1. This computes a yt-dlp
`playlist_items` spec (original 1-based indices, in the desired order) plus
a map from original index -> new download-order position, used to translate
yt-dlp's own playlist_index into "N of M" during progress reporting.
"""

from services.downloader import _ordered_playlist_items


def test_sorts_ascending_by_resolved_episode_number():
    # Platform returned newest-first; entry 1 (original index) is episode 3.
    entries = [
        {"title": "Show - Episode 3"},
        {"title": "Show - Episode 2"},
        {"title": "Show - Episode 1"},
    ]
    playlist_items, position_map = _ordered_playlist_items(entries)
    assert playlist_items == "3,2,1"
    # Original index 3 (episode 1) should now be download position 1.
    assert position_map == {3: 1, 2: 2, 1: 3}


def test_unresolvable_entries_fall_back_to_original_position():
    entries = [
        {"title": "Episode 2 has a marker"},
        {"title": "No marker at all here"},
        {"title": "Episode 1 has a marker"},
    ]
    playlist_items, position_map = _ordered_playlist_items(entries)
    # Numbered entries: (2, original_index=1), (1, original_index=3).
    # Unresolvable entry keeps its original index (2) as its sort key.
    # Sort by number: (1, 3), (2, 1), (2, 2) -- ties keep relative order
    # since Python's sort is stable.
    assert playlist_items == "3,1,2"
    assert position_map == {3: 1, 1: 2, 2: 3}


def test_entries_that_failed_extraction_entirely_are_skipped():
    # ignoreerrors=True means a failed entry comes back as None -- yt-dlp
    # never attempts it regardless, so it must not appear in the spec.
    entries = [{"title": "Episode 1"}, None, {"title": "Episode 2"}]
    playlist_items, position_map = _ordered_playlist_items(entries)
    assert playlist_items == "1,3"
    assert position_map == {1: 1, 3: 2}


def test_empty_entries_produces_empty_spec():
    playlist_items, position_map = _ordered_playlist_items([])
    assert playlist_items == ""
    assert position_map == {}


def test_all_entries_unresolvable_keeps_original_platform_order():
    entries = [{"title": "First"}, {"title": "Second"}, {"title": "Third"}]
    playlist_items, position_map = _ordered_playlist_items(entries)
    assert playlist_items == "1,2,3"
    assert position_map == {1: 1, 2: 2, 3: 3}
