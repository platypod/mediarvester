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


def test_trailing_hash_number_is_treated_as_an_episode_marker():
    # This creator's "Run Lore" sub-series has no "episode"/"ep" word at all,
    # just a trailing "#N" -- confirmed missed in production (2026-08-22),
    # leaving a whole sub-series unrenamed.
    info = {"title": "Farum Azula, perdu dans le Temps - Elden Ring Run Lore #21"}
    assert resolve_episode(info) == (21, "Farum Azula, perdu dans le Temps - Elden Ring Run Lore")


def test_trailing_hash_number_with_no_preceding_dash():
    info = {"title": "Il fait chaud là-dedans non ? Elden Ring Run Lore #13"}
    assert resolve_episode(info) == (13, "Il fait chaud là-dedans non ? Elden Ring Run Lore")


def test_hash_number_not_at_end_is_not_treated_as_an_episode_marker():
    # A mid-title "#N" (a real hashtag, a rank, ...) is not anchored to the
    # end and should not be trusted the same way.
    assert resolve_episode({"title": "My #1 Favorite Game - Highlights"}) is None


def test_marker_trims_everything_from_the_match_onward():
    # The dash-prefixed marker and anything after it is redundant once the
    # number becomes a filename prefix -- confirm the whole tail is dropped,
    # not just the marker word itself.
    info = {"title": "Le Retour du Cerf - Elden Ring - Épisode 27"}
    assert resolve_episode(info) == (27, "Le Retour du Cerf - Elden Ring")


def test_mid_title_hash_number_followed_by_a_dash_is_an_episode_marker():
    # "<show> #N - <episode title>": the marker closes its segment, so the
    # number is trustworthy even though it isn't at the end. Confirmed
    # missed in production (2026-09-17) -- a followed series downloaded 22
    # episodes, none of them renamed.
    info = {
        "title": "VOD ► LE TUNNEL #16 -  ORQUES & GOBELINS DU VIEUX MONDE "
        "- LORE WARHAMMER FANTASY"
    }
    assert resolve_episode(info) == (
        16,
        "ORQUES & GOBELINS DU VIEUX MONDE - LORE WARHAMMER FANTASY",
    )


def test_mid_title_marker_keeps_the_tail_not_the_leading_show_name():
    # The opposite trim direction from a terminal marker, and the whole
    # reason this is a separate pattern: trimming this one the terminal way
    # yields the show name ("VOD ► LE TUNNEL") for every episode, leaving
    # files that differ only by their number prefix.
    info = {"title": "VOD ► LE TUNNEL #22 - LES SALAMANDERS - LORE WARHAMMER 40K"}
    assert resolve_episode(info) == (22, "LES SALAMANDERS - LORE WARHAMMER 40K")


def test_mid_title_hash_number_not_followed_by_a_dash_is_still_ignored():
    # Unchanged from before: "#1" here modifies the word after it (a rank),
    # it doesn't close a segment. The " - " delimiter is the only thing that
    # distinguishes this from a real marker.
    assert resolve_episode({"title": "My #1 Favorite Game - Highlights"}) is None


def test_mid_title_marker_tolerates_irregular_spacing_around_the_dash():
    # This creator's own titles are inconsistent here (a double space after
    # the dash on some episodes, a single on others).
    single = {"title": "Show #7 - A Title"}
    double = {"title": "Show #7 -  A Title"}
    assert resolve_episode(single) == resolve_episode(double) == (7, "A Title")


def test_marker_word_wins_over_a_mid_title_hash_number():
    # An explicit "Épisode N" is what the creator actually labeled the
    # episode as, so it outranks a "#N - " earlier in the title.
    info = {"title": "Best of #3 - Le Retour du Cerf - Épisode 27"}
    assert resolve_episode(info) == (27, "Best of #3 - Le Retour du Cerf")


def test_trailing_hash_number_still_wins_over_the_segment_pattern():
    # Both shapes are present; the terminal one is checked first and trims
    # the other way, keeping everything before it.
    info = {"title": "Series #4 - Something Happened #12"}
    assert resolve_episode(info) == (12, "Series #4 - Something Happened")


def test_mid_title_marker_with_no_tail_after_the_dash_is_ignored():
    # Nothing usable follows the delimiter, so there is no display title to
    # fall back to -- structured metadata (or nothing) handles it instead.
    assert resolve_episode({"title": "Show #9 -"}) is None


def test_mid_title_marker_with_no_leading_show_name():
    assert resolve_episode({"title": "#5 - Something"}) == (5, "Something")
