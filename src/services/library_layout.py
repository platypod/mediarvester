"""Decide where a downloaded item belongs in the Jellyfin-facing library, and
write the metadata Jellyfin needs to describe it without the internet.

The library is split in two roots under MEDIA_ROOT:

    series/<Show>/Season 01/<Show> - S01E04 - <Title>.<ext>
    singles/<Creator>/<Title>.<ext>

`series` is served as a Jellyfin *tvshows* library and `singles` as a *movies*
one. The split exists because those two collection types want incompatible
layouts: a tvshows library treats every top-level folder as a series, so a
loose creator video dropped into one becomes a bogus single-episode show with
no metadata.

WHY NOT A JELLYFIN METADATA PLUGIN: Jellyfin reads Kodi-style .nfo sidecars
natively, so the metadata this module writes needs no plugin, no API key and
no network -- and it cannot break on a Jellyfin upgrade the way a compiled
provider plugin does. yt-dlp already hands us everything the NFO needs.

IMPORTANT, and not something this code can enforce: the two libraries must
have their *online* metadata providers switched off in Jellyfin. Left on,
Jellyfin will match a show called "LE TUNNEL" against TheTVDB and attach some
unrelated programme's artwork and episode titles on top of these files.
"""

import html
import json
import urllib.request
import logging
import os
import re
import subprocess
from os import environ
from pathlib import Path

from services.episode_naming import resolve_episode

logger = logging.getLogger(__name__)

SERIES_SUBDIR = environ.get("SERIES_SUBDIR", "series")
SINGLES_SUBDIR = environ.get("SINGLES_SUBDIR", "singles")

# YouTube has no concept of seasons, so a NEW show lands in one. Season-per-year
# was the alternative and was rejected: it splits a series that happens to run
# across New Year, and every episode number these creators use is continuous
# across that boundary anyway (LE TUNNEL #1-#22, KCD2 #1-#29).
#
# An EXISTING show may still be genuinely multi-season -- one library folder was
# already laid out by hand as Season 01 (2020) / 02 (2023) / 03 (2024) -- so
# `season_for` reads what is on disk and only falls back to this default.
DEFAULT_SEASON = 1
SEASON = DEFAULT_SEASON  # back-compat alias for the default

_SEASON_DIR_RE = re.compile(r"^Season (\d+)", re.IGNORECASE)

# The SxxExx marker inside a built filename stem.
_STEM_MARKER_RE = re.compile(r" - S(\d{2})E(\d{2}) - ")

# A playlist whose name means "everything this channel posted" is not a serial.
# Treating one as a show produces a "series" of hundreds of unrelated uploads
# with no meaningful episode order -- Feldup's uploads tab (138 videos) is the
# example this was written against.
_DUMP_RE = re.compile(r"^(vid[ée]os|les vid[ée]os|videos|.+ - videos)$", re.IGNORECASE)

# yt-dlp renders an absent field as the literal "NA" in some template forms, so
# a folder/playlist by that name is a missing title, not a real playlist.
_NOT_A_PLAYLIST = {"", "NA", "None"}

_VIDEO_EXTS = (".webm", ".mkv", ".mp4", ".m4a", ".mov")
_IMAGE_EXTS = (".webp", ".jpg", ".jpeg", ".png")


def sanitize(name: str) -> str:
    """Make a title safe as one path component.

    Only the separator is genuinely unusable; yt-dlp has already replaced the
    other awkward characters with lookalikes by the time we see a filename.
    Trailing dots and spaces are stripped because they are silently dropped by
    some filesystems, which would make the path we record differ from the path
    that exists.
    """
    return (name or "").replace("/", "-").strip().rstrip(". ") or "Untitled"


def creator_of(entry: dict) -> str:
    """Same fallback chain as the download output template."""
    for key in ("uploader", "channel", "creator"):
        if entry.get(key):
            return sanitize(entry[key])
    return "Unsorted"


def playlist_of(entry: dict) -> str | None:
    name = entry.get("playlist_title") or entry.get("playlist") or ""
    name = name.strip()
    if name in _NOT_A_PLAYLIST or _DUMP_RE.match(name):
        return None
    return sanitize(name)


def show_name(entry: dict) -> str | None:
    """"<Creator> - <Playlist>", the folder a tvshows library reads as a series.

    The creator is included because a playlist name alone collides across
    channels -- two of the followed creators each have an "Outer Wilds" series,
    and a tvshows library keys a show on its folder name, so the two would
    merge into one show with interleaved episodes.
    """
    playlist = playlist_of(entry)
    if not playlist:
        return None
    return sanitize(f"{creator_of(entry)} - {playlist}")


def episode_number(entry: dict) -> int | None:
    resolved = resolve_episode(entry)
    return resolved[0] if resolved else None


def episode_title(entry: dict) -> str:
    """The title with the episode marker trimmed, when one was found in it."""
    resolved = resolve_episode(entry)
    if resolved and resolved[1]:
        return sanitize(resolved[1])
    return sanitize(entry.get("title") or "Untitled")


def classify(entry: dict, media_root: str | None = None) -> str:
    """"series" or "singles".

    A real playlist plus a usable episode number is a series. An unnumbered
    entry normally is not -- it cannot be expressed as SxxExx, and Jellyfin
    will not order a show whose episodes have no numbers, so it is better
    served as a standalone item than as an unorderable episode.

    The exception is an unnumbered entry belonging to a show that ALREADY
    EXISTS: most of these creators never number anything (of 31 real series
    folders, 22 had no in-title numbers at all), and those shows are ordered
    by upload date instead. Once such a show exists, its next episode must
    join it rather than being filed away as a movie -- otherwise a show
    migrated as a show would silently stop growing, with new episodes landing
    in a different library. `next_episode_number` supplies the number in that
    case; `media_root` is what makes the existing show visible here.
    """
    if not show_name(entry):
        return "singles"
    if episode_number(entry):
        return "series"
    if media_root and _season_dir(entry, media_root).is_dir():
        return "series"
    return "singles"


def _show_dir(entry: dict, media_root: str) -> Path:
    return Path(media_root) / SERIES_SUBDIR / (show_name(entry) or "")


def season_for(entry: dict, media_root: str | None) -> int:
    """The season a new episode of this show belongs to.

    The highest season already present, because a show that has run for three
    seasons is still running in its third -- appending to Season 01 would file
    today's episode under the oldest one. Falls back to DEFAULT_SEASON for a
    show that does not exist yet, which is every new YouTube series.
    """
    if not media_root:
        return DEFAULT_SEASON
    show_dir = _show_dir(entry, media_root)
    if not show_dir.is_dir():
        return DEFAULT_SEASON
    seasons = [int(m.group(1)) for name in os.listdir(show_dir)
               if (m := _SEASON_DIR_RE.match(name)) and (show_dir / name).is_dir()]
    return max(seasons) if seasons else DEFAULT_SEASON


def _season_dir(entry: dict, media_root: str) -> Path:
    return _show_dir(entry, media_root) / f"Season {season_for(entry, media_root):02d}"


def next_episode_number(entry: dict, media_root: str) -> int:
    """One past the highest episode already in this show.

    Used for a show whose creator numbers nothing: the running order is upload
    order, and an episode arriving now is by definition the latest.
    """
    directory = _season_dir(entry, media_root)
    season = season_for(entry, media_root)
    highest = 0
    if directory.is_dir():
        for name in os.listdir(directory):
            match = re.search(rf"S{season:02d}E(\d+) - ", name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def target_for(entry: dict, media_root: str, ext: str, taken: set | None = None) -> tuple[str, str]:
    """Return `(absolute directory, filename stem)` for this entry.

    `taken` is an optional set of stems already claimed in this run, used so a
    batch (a migration, a playlist download) does not plan two files onto one
    path before either exists on disk.
    """
    if classify(entry, media_root) == "series":
        show = show_name(entry)
        season = season_for(entry, media_root)
        number = episode_number(entry) or next_episode_number(entry, media_root)
        directory = str(_season_dir(entry, media_root))
        number = _free_number(directory, show, season, number, entry, taken)
        stem = f"{show} - S{season:02d}E{number:02d} - {episode_title(entry)}"
    else:
        directory = os.path.join(media_root, SINGLES_SUBDIR, creator_of(entry))
        stem = sanitize(entry.get("title") or "Untitled")
    return directory, stem


def _free_number(directory: str, show: str, season: int, number: int, entry: dict, taken: set | None) -> int:
    """Keep the creator's episode number unless it is already used by a
    DIFFERENT video, then take the next free one.

    Two distinct videos claiming one number is not hypothetical: a followed
    series had two episodes both titled "... #12" (different videos, five days
    apart). Left alone they collapse onto one SxxExx path and Jellyfin silently
    shows whichever it scanned first, so the other episode simply vanishes from
    the library. Re-using the number for the same video is fine and expected --
    that is a re-download, and it should land on its own path.
    """
    video_id = entry.get("id")
    for candidate in range(number, number + 1000):
        stem_prefix = f"{show} - S{season:02d}E{candidate:02d} - "
        if taken is not None and any(s.startswith(stem_prefix) for s in taken):
            continue
        clash = _existing_video_id(directory, stem_prefix)
        if clash is None or clash == video_id:
            if candidate != number:
                logger.info(
                    "episode %d of %r is taken by another video, using %d instead",
                    number, show, candidate,
                )
            return candidate
    return number


def _existing_video_id(directory: str, stem_prefix: str) -> str | None:
    """The video id already occupying this episode slot, if any."""
    if not os.path.isdir(directory):
        return None
    for name in os.listdir(directory):
        if not name.startswith(stem_prefix) or not name.endswith(".info.json"):
            continue
        try:
            return json.load(open(os.path.join(directory, name))).get("id") or ""
        except Exception:
            return ""
    for name in os.listdir(directory):
        if name.startswith(stem_prefix) and name.lower().endswith(_VIDEO_EXTS):
            return ""  # occupied, but by something we cannot identify
    return None


# --------------------------------------------------------------------------
# NFO sidecars
# --------------------------------------------------------------------------

def _tag(name: str, value) -> str:
    if value in (None, ""):
        return ""
    return f"  <{name}>{html.escape(str(value), quote=False)}</{name}>\n"


def _aired(entry: dict) -> str:
    date = entry.get("upload_date") or ""
    return f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else ""


def episode_nfo(entry: dict, number: int, season: int = DEFAULT_SEASON) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<episodedetails>\n'
        + _tag("title", episode_title(entry))
        + _tag("season", season)
        + _tag("episode", number)
        + _tag("plot", entry.get("description"))
        + _tag("aired", _aired(entry))
        + _tag("studio", creator_of(entry))
        + _tag("runtime", round((entry.get("duration") or 0) / 60) or None)
        + f'  <uniqueid type="youtube" default="true">{html.escape(str(entry.get("id") or ""))}</uniqueid>\n'
        + "</episodedetails>\n"
    )


def movie_nfo(entry: dict) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<movie>\n'
        + _tag("title", entry.get("title"))
        + _tag("plot", entry.get("description"))
        + _tag("premiered", _aired(entry))
        + _tag("studio", creator_of(entry))
        + _tag("director", creator_of(entry))
        + _tag("runtime", round((entry.get("duration") or 0) / 60) or None)
        + f'  <uniqueid type="youtube" default="true">{html.escape(str(entry.get("id") or ""))}</uniqueid>\n'
        + "</movie>\n"
    )


def tvshow_nfo(entry: dict) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<tvshow>\n'
        + _tag("title", show_name(entry))
        + _tag("plot", f"{playlist_of(entry)} by {creator_of(entry)}.")
        + _tag("studio", creator_of(entry))
        + "</tvshow>\n"
    )


# Jellyfin looks for artwork by suffix next to the media file.
IMAGE_SUFFIX = {"series": "-thumb", "singles": "-poster"}


def place(abs_path: str, entry: dict, media_root: str) -> str:
    """Move a just-downloaded file and its sidecars into the library layout,
    writing the NFO metadata alongside. Returns the file's new absolute path.

    Every failure here returns the ORIGINAL path rather than raising: the
    download itself succeeded, and a file sitting in the wrong folder is a
    tidiness problem, not a reason to fail the job and re-fetch gigabytes.
    """
    src = Path(abs_path)
    if not src.exists():
        return abs_path
    kind = classify(entry, media_root)
    try:
        directory, stem = target_for(entry, media_root, src.suffix)
        os.makedirs(directory, exist_ok=True)
        dest = Path(directory) / f"{stem}{src.suffix}"
        if dest != src:
            if dest.exists():
                logger.warning("%s already exists, leaving %s where it is", dest.name, src.name)
                return abs_path
            src.rename(dest)

        _move_sidecars(src, Path(directory), stem, kind)
        _ensure_artwork(Path(directory), stem, kind, entry, dest)
        _write_metadata(Path(directory), stem, entry, kind)
        _prune_empty(src.parent, media_root)
        return str(dest)
    except OSError as exc:
        logger.warning("could not place %s into the library layout: %s", src.name, exc)
        return abs_path


def _prune_empty(directory: Path, media_root: str) -> None:
    """Remove the directory the download came from, once it is empty.

    yt-dlp writes to `{MEDIA_ROOT}/{uploader}/{playlist}/` and `place` moves
    the result into the library tree, so every download leaves its original
    folder behind empty. Unpruned, MEDIA_ROOT slowly refills with hollow
    creator folders -- the exact clutter the library split exists to remove.

    Walks upward while each level is empty, and stops at MEDIA_ROOT itself and
    at the library roots, which must survive even when they happen to be empty.
    """
    root = Path(media_root).resolve()
    keep = {root, root / SERIES_SUBDIR, root / SINGLES_SUBDIR}
    current = directory.resolve()
    while current not in keep and root in current.parents:
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _move_sidecars(src: Path, directory: Path, stem: str, kind: str) -> None:
    """Carry the .info.json and the thumbnail across, renaming the image to the
    suffix Jellyfin looks for (`-thumb` / `-poster`). yt-dlp writes both under
    the media file's own stem, so they would be orphaned by the rename."""
    old_stem = src.stem
    for name in sorted(os.listdir(src.parent)):
        if not name.startswith(old_stem):
            continue
        rest = name[len(old_stem):]
        if not rest.startswith("."):
            continue  # shares a prefix but is a different file
        suffix = rest.lower()
        if suffix.endswith(_IMAGE_EXTS):
            target = directory / f"{stem}{IMAGE_SUFFIX[kind]}{rest}"
        elif suffix == ".info.json":
            target = directory / f"{stem}.info.json"
        else:
            continue
        try:
            if not target.exists():
                (src.parent / name).rename(target)
        except OSError as exc:
            logger.warning("could not move sidecar %s: %s", name, exc)


def _fetch_thumbnail(entry: dict, dest: Path) -> bool:
    """Download the item's own YouTube thumbnail.

    yt-dlp's `writethumbnail` normally leaves one beside the media, but it
    silently produces nothing for some items -- more than half of one real
    library had no artwork at all. The info dict still carries the URL, so
    fetch it rather than leaving the item blank. Best resolution first.
    """
    urls = []
    for t in reversed(entry.get("thumbnails") or []):
        if t.get("url"):
            urls.append(t["url"])
    if entry.get("thumbnail"):
        urls.insert(0, entry["thumbnail"])
    for url in urls[:4]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=20).read()
            if len(data) < 1000:      # an error page, not an image
                continue
            dest.write_bytes(data)
            return True
        except Exception as exc:
            logger.debug("thumbnail fetch failed for %s: %s", url, exc)
    return False


def _extract_frame(video: Path, dest: Path, duration: float | None) -> bool:
    """Last resort: grab a frame from the video itself.

    Used only when the item has no YouTube thumbnail to fetch -- older or
    hand-curated content whose source video cannot be identified. A frame is
    honest about what the item contains, and storing it here means Jellyfin
    does not have to re-derive one into a cache that is lost on a library
    rebuild.

    The seek lands ~10% in rather than at the start: the opening seconds of a
    video are routinely black, a fade, or a channel intro identical across
    every episode of a series, all of which make useless artwork.
    """
    offset = max(1.0, (duration or 0) * 0.1) if duration else 30.0
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-ss", f"{offset:.2f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "3", str(dest)],
            capture_output=True, timeout=180, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("frame extraction failed for %s: %s", video.name, exc)
        return False
    if dest.exists() and dest.stat().st_size > 1000:
        return True
    dest.unlink(missing_ok=True)
    return False


def _ensure_artwork(directory: Path, stem: str, kind: str, entry: dict, video: Path) -> None:
    """YouTube's own thumbnail if there is one, otherwise a frame from the file."""
    if any((directory / f"{stem}{IMAGE_SUFFIX[kind]}{e}").exists() for e in _IMAGE_EXTS):
        return
    dest = directory / f"{stem}{IMAGE_SUFFIX[kind]}.jpg"
    if _fetch_thumbnail(entry, dest):
        return
    _extract_frame(video, dest, entry.get("duration"))


def _write_show_artwork(show_dir: Path, season_dir: Path, stem: str, entry: dict) -> None:
    """Give the show folder a poster Jellyfin will actually use.

    A tvshows library looks for poster/folder/cover at the SHOW root, not on
    the episodes, so a series whose episodes all have artwork still shows up as
    a blank tile. YouTube has no artwork for a playlist as such -- its cover is
    just the first video's thumbnail -- so the episode that creates the show
    supplies it, which reproduces what YouTube itself displays.
    """
    if any((show_dir / f"poster{e}").exists() for e in _IMAGE_EXTS):
        return
    for ext in _IMAGE_EXTS:
        src = season_dir / f"{stem}{IMAGE_SUFFIX['series']}{ext}"
        if src.exists():
            try:
                (show_dir / f"poster{ext}").write_bytes(src.read_bytes())
                return
            except OSError as exc:
                logger.warning("could not write the show poster for %s: %s", show_dir.name, exc)
                return
    if _fetch_thumbnail(entry, show_dir / "poster.jpg"):
        return
    for ext in (".webm", ".mkv", ".mp4"):
        episode = season_dir / f"{stem}{ext}"
        if episode.exists():
            _extract_frame(episode, show_dir / "poster.jpg", entry.get("duration"))
            return


def _write_metadata(directory: Path, stem: str, entry: dict, kind: str) -> None:
    try:
        if kind == "series":
            # Matched as a pattern, never by splitting on " - S": an episode
            # whose TITLE starts with S ("... - S01E39 - Satanés Égouts")
            # makes a rsplit grab the title's own S and the parse dies, so the
            # episode silently gets no metadata at all. 17 real files hit this.
            marker = _STEM_MARKER_RE.search(stem)
            if not marker:
                raise ValueError(f"no SxxExx marker in {stem!r}")
            season, number = int(marker.group(1)), int(marker.group(2))
            (directory / f"{stem}.nfo").write_text(
                episode_nfo(entry, number, season), encoding="utf-8")
            # One per show, beside the Season folder, not inside it.
            show_nfo = directory.parent / "tvshow.nfo"
            if not show_nfo.exists():
                show_nfo.write_text(tvshow_nfo(entry), encoding="utf-8")
            _write_show_artwork(directory.parent, directory, stem, entry)
        else:
            (directory / f"{stem}.nfo").write_text(movie_nfo(entry), encoding="utf-8")
    except (OSError, ValueError, IndexError) as exc:
        logger.warning("could not write NFO for %s: %s", stem, exc)
