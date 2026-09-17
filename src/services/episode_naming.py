"""Resolve an episode number (and a clean display title) for a downloaded
item, if a number can be determined.

Creators format episode numbers in their titles inconsistently -- "Épisode 5",
"Ep 5", "Ep5", "Ép 5" all show up from the same channel. Rather than one
strict pattern, a short list of known marker-word patterns is tried in turn,
falling back to structured yt-dlp metadata first since it's unambiguous when
present. A bare trailing number with no marker word at all (e.g. a title
ending in "... 21") is deliberately NOT treated as an episode number here --
across arbitrary creators that's too easy to false-positive on (release
years, part counts, resolution/quality tags, ...). `playlist_index` is the
intended fallback for that case: it's populated whenever the download came
from a playlist/channel-tab context, which titles-with-a-bare-number tend to
rely on anyway (no in-title marker because the platform's own ordering was
assumed to be enough).

A trailing "#N" (e.g. "... Run Lore #21") IS treated as a marker despite
having no word attached -- "#" ahead of a number is specifically an episode/
part indicator in creator titles and doesn't collide with years or quality
tags the way a bare number does, so it's safe to trust anchored at the end.

A "#N" in the MIDDLE is trusted too, but only when a " - " delimiter follows
it directly ("VOD ► LE TUNNEL #16 -  ORQUES & GOBELINS ..."), which is the
other common shape: show name, marker, then the episode's own title. What
makes that safe is the delimiter, not the "#" -- compare "My #1 Favorite
Game - Highlights", where "#1" is a rank modifying the next word rather than
a marker closing a segment. Requiring the number to END its segment is what
separates the two, and a bare "#N" followed by more words is still ignored.

The two shapes trim in OPPOSITE directions, which is why they are separate
patterns rather than one relaxed regex. For a terminal marker the content is
everything before it; for a mid-title marker the show name comes first and
the episode's real title is what FOLLOWS the marker. Trimming a mid-title
match the terminal way collapsed every episode of a followed series to the
show name itself -- "VOD ► LE TUNNEL #16 - ORQUES & GOBELINS DU VIEUX MONDE"
resolving to "VOD ► LE TUNNEL", i.e. 22 files differing only by their number
prefix. Confirmed against the real AlphaReplay catalogue, 2026-09-17.
"""

import re

_MARKER_PATTERNS = [
    re.compile(r"\s*-?\s*\b[ée]pisode\s*#?\s*(\d{1,4})\b", re.IGNORECASE),
    re.compile(r"\s*-?\s*\b[ée]p\.?\s*#?\s*(\d{1,4})\b", re.IGNORECASE),
    # A trailing "#N" with no marker word at all (e.g. "... Run Lore #21").
    # Restricted to the end of the title (unlike the bare-number case the
    # module docstring rules out) because "#" is specifically an episode/part
    # marker in creator titles, not something a year or quality tag ever
    # collides with -- so anchoring it there is enough to stay safe without
    # needing a marker word.
    re.compile(r"\s*-?\s*#\s*(\d{1,4})\s*$"),
]

# "<show> #N - <episode title>". Checked only after every _MARKER_PATTERNS
# entry, so an explicit marker word anywhere in the title still wins and a
# title ending in "#N" is still read as a terminal marker.
#
# `\s+-\s+` is the whole safeguard: the number must close its segment. That
# is what keeps "My #1 Favorite Game - Highlights" from matching -- there a
# word, not a delimiter, follows the number. The lookahead additionally
# requires something non-empty after the delimiter, since a title that ends
# in "#N -" carries no tail to use as the display title.
_SEGMENT_MARKER_PATTERN = re.compile(r"#\s*(\d{1,4})\s+-\s+(?=\S)")


def resolve_episode(info: dict) -> tuple[int, str] | None:
    """Return `(number, display_title)`, or `None` if no number can be
    resolved. `display_title` has the matched marker (and everything from it
    onward -- e.g. a trailing "- Show Name - Épisode 8") stripped, since that
    text is now redundant once the number becomes a filename prefix. For a
    mid-title "#N - " marker the redundant part is the LEADING show name
    instead, so everything up to and including the marker is dropped and the
    tail is kept. When the number instead comes from structured metadata (no
    in-title match to trim from), the title is returned unchanged."""
    title = info.get("title") or ""

    # An in-title marker is checked first (ahead of structured metadata):
    # it's what the creator explicitly labeled the episode as, and matching
    # it is also what lets the redundant tail get trimmed from the title.
    for pattern in _MARKER_PATTERNS:
        match = pattern.search(title)
        if match:
            return int(match.group(1)), title[: match.start()].rstrip(" -").strip()

    match = _SEGMENT_MARKER_PATTERN.search(title)
    if match:
        return int(match.group(1)), title[match.end() :].strip()

    for key in ("episode_number", "playlist_index"):
        value = info.get(key)
        if isinstance(value, int) and value > 0:
            return value, title

    return None
