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
"""

import re

_MARKER_PATTERNS = [
    re.compile(r"\s*-?\s*\b[ée]pisode\s*#?\s*(\d{1,4})\b", re.IGNORECASE),
    re.compile(r"\s*-?\s*\b[ée]p\.?\s*#?\s*(\d{1,4})\b", re.IGNORECASE),
]


def resolve_episode(info: dict) -> tuple[int, str] | None:
    """Return `(number, display_title)`, or `None` if no number can be
    resolved. `display_title` has the matched marker (and everything from it
    onward -- e.g. a trailing "- Show Name - Épisode 8") stripped, since that
    text is now redundant once the number becomes a filename prefix. When the
    number instead comes from structured metadata (no in-title match to trim
    from), the title is returned unchanged."""
    title = info.get("title") or ""

    # An in-title marker is checked first (ahead of structured metadata):
    # it's what the creator explicitly labeled the episode as, and matching
    # it is also what lets the redundant tail get trimmed from the title.
    for pattern in _MARKER_PATTERNS:
        match = pattern.search(title)
        if match:
            return int(match.group(1)), title[: match.start()].rstrip(" -").strip()

    for key in ("episode_number", "playlist_index"):
        value = info.get(key)
        if isinstance(value, int) and value > 0:
            return value, title

    return None
