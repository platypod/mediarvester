"""services/episode_naming.py -- resolve_episode.

Creators format episode markers inconsistently; this is the heuristic that
both the on-disk renaming (_apply_episode_prefix) and the playlist download
ordering (_ordered_playlist_items) trust. A false positive or a missed
marker silently misfiles or misorders real content, so the marker patterns
and the fallback chain are worth pinning down explicitly.
"""

from services.episode_naming import resolve_episode


def test_episode_marker_with_accent_and_hash():
    assert resolve_episode({"title": "Some Show - Épisode #14"}) == (14, "Some Show")


def test_episode_marker_without_accent():
    assert resolve_episode({"title": "Some Show - Episode 14"}) == (14, "Some Show")


def test_ep_abbreviation_with_period():
    assert resolve_episode({"title": "Cool Video - Ep. 3"}) == (3, "Cool Video")


def test_ep_abbreviation_no_period_no_space():
    assert resolve_episode({"title": "Cool Video - Ep3"}) == (3, "Cool Video")


def test_marker_is_case_insensitive():
    assert resolve_episode({"title": "Show - ÉPISODE 7"}) == (7, "Show")


def test_marker_takes_priority_over_structured_metadata():
    # An in-title marker should win even when episode_number disagrees --
    # it's what the creator explicitly labeled this episode as.
    info = {"title": "Show - Episode 5", "episode_number": 99}
    assert resolve_episode(info) == (5, "Show")


def test_falls_back_to_episode_number_when_no_marker():
    info = {"title": "A Title With No Marker", "episode_number": 12}
    assert resolve_episode(info) == (12, "A Title With No Marker")


def test_falls_back_to_playlist_index_when_no_marker_or_episode_number():
    info = {"title": "A Title With No Marker", "playlist_index": 4}
    assert resolve_episode(info) == (4, "A Title With No Marker")


def test_episode_number_takes_priority_over_playlist_index():
    info = {"title": "A Title", "episode_number": 12, "playlist_index": 4}
    assert resolve_episode(info) == (12, "A Title")


def test_bare_trailing_number_is_not_treated_as_an_episode_marker():
    # Deliberate: a title ending in "... 2026" (a year) or "... 4K" would
    # false-positive constantly if a bare number alone counted.
    assert resolve_episode({"title": "Big Buck Bunny 60fps 4K"}) is None


def test_zero_playlist_index_is_not_treated_as_a_valid_episode_number():
    info = {"title": "A Title", "playlist_index": 0}
    assert resolve_episode(info) is None


def test_negative_episode_number_is_not_treated_as_valid():
    info = {"title": "A Title", "episode_number": -1}
    assert resolve_episode(info) is None


def test_no_marker_and_no_structured_metadata_returns_none():
    assert resolve_episode({"title": "Just A Plain Title"}) is None


def test_missing_title_does_not_raise():
    assert resolve_episode({}) is None


def test_marker_trims_everything_from_the_match_onward():
    # The dash-prefixed marker and anything after it is redundant once the
    # number becomes a filename prefix -- confirm the whole tail is dropped,
    # not just the marker word itself.
    info = {"title": "Le Retour du Cerf - Elden Ring - Épisode 27"}
    assert resolve_episode(info) == (27, "Le Retour du Cerf - Elden Ring")
