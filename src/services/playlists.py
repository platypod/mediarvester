"""Local playlist ownership: which show a video belongs to.

A video fetched on its own -- by a channel source, a manual URL, an
auto-retry -- carries no playlist context in yt-dlp's info, and YouTube has no
"which playlists contain this video" lookup. So the playlists the owner
cares about (every followed playlist Source, plus any playlist downloaded in
full) are recorded here together with their member video ids, and placement
asks this registry instead of the network.

Locally a video belongs to AT MOST ONE playlist even though YouTube allows
several: followed playlists outrank merely-downloaded ones, then the earliest
recorded wins, and nothing past the first match is consulted.

A video can also be downloaded BEFORE its playlist lists it (the channel poll
fires the moment it is uploaded, the creator files it later). `reconcile_playlist`
handles that: once a playlist's membership shows a standalone file, the file is
moved into the show -- but only if it was downloaded after the playlist was
first recorded. Older files are merely reported (`preview_backfill`).
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from os import environ
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sqlalchemy import delete, select

from db import MediaItem, Playlist, PlaylistMember, async_session
from services import library_layout

logger = logging.getLogger(__name__)

# How old a followed playlist's membership snapshot may be before a lookup
# for an unknown video re-fetches it. The poller refreshes it every poll
# anyway; this only bounds the extra requests a manual download can cause.
REFRESH_TTL = timedelta(seconds=int(environ.get("PLAYLIST_REFRESH_TTL_SECONDS", "600")))

# Serializes every process-wide YouTube scan (source polls and the lazy
# refreshes below) so they never run concurrently -- see poller.py.
scan_lock = asyncio.Lock()

_VIDEO_ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/)([\w-]{6,})")


def is_playlist_url(url: str) -> bool:
    parsed = urlparse(url)
    return "list" in parse_qs(parsed.query) or parsed.path.rstrip("/").lower().endswith("/playlist")


def playlist_id_from_url(url: str) -> str | None:
    values = parse_qs(urlparse(url).query).get("list")
    return values[0] if values else None


def video_id_of(url: str | None) -> str | None:
    match = _VIDEO_ID_RE.search(url or "")
    return match.group(1) if match else None


def fetch_playlist(url: str, owner: str) -> dict:
    """Cheap flat listing of a playlist (id, title, uploader, entries).
    Blocking -- run it in an executor. Kept as its own seam for tests."""
    from services.downloader import _flat_extract_info

    return _flat_extract_info(url, owner)


def _member_ids(info: dict) -> list[str]:
    return [e["id"] for e in (info.get("entries") or []) if e and e.get("id")]


async def fetch_member_ids(url: str, owner: str) -> tuple[dict, list[str]]:
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, fetch_playlist, url, owner)
    return info, _member_ids(info)


async def record_playlist(
    *,
    owner: str,
    playlist_id: str,
    title: str,
    url: str,
    creator: str | None,
    member_ids: list[str],
    followed: bool = False,
    source_id: int | None = None,
) -> Playlist | None:
    """Create or update a playlist and replace its member list.

    Returns None for a playlist that is not a show (a channel's uploads tab,
    an unnamed one) -- those must never claim videos.
    """
    if library_layout.playlist_of({"playlist_title": title}) is None:
        return None
    async with async_session() as session:
        pl = (
            await session.execute(
                select(Playlist).where(Playlist.owner == owner, Playlist.playlist_id == playlist_id)
            )
        ).scalar_one_or_none()
        if pl is None:
            pl = Playlist(owner=owner, playlist_id=playlist_id, title=title, url=url, creator=creator)
            session.add(pl)
            await session.flush()
        pl.title, pl.url = title, url
        pl.creator = creator or pl.creator
        pl.followed = pl.followed or followed
        pl.source_id = source_id if source_id is not None else pl.source_id
        pl.members_refreshed_at = datetime.utcnow()
        await session.execute(delete(PlaylistMember).where(PlaylistMember.playlist_pk == pl.id))
        session.add_all(PlaylistMember(playlist_pk=pl.id, video_id=v) for v in dict.fromkeys(member_ids))
        await session.commit()
        return pl


async def owning_playlist(owner: str, video_id: str) -> Playlist | None:
    async with async_session() as session:
        return (
            await session.execute(
                select(Playlist)
                .join(PlaylistMember, PlaylistMember.playlist_pk == Playlist.id)
                .where(Playlist.owner == owner, PlaylistMember.video_id == video_id)
                .order_by(Playlist.followed.desc(), Playlist.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def resolve_owner(owner: str, entry: dict) -> Playlist | None:
    """The playlist that owns this video, or None. Local lookup first; when
    that misses, re-fetch followed playlists whose snapshot is stale (same
    creator first), stopping at the first one that contains the video."""
    video_id = entry.get("id")
    if not video_id:
        return None
    found = await owning_playlist(owner, video_id)
    if found:
        return found

    uploader = entry.get("uploader") or entry.get("channel") or entry.get("creator")
    cutoff = datetime.utcnow() - REFRESH_TTL
    async with async_session() as session:
        stale = (
            await session.execute(
                select(Playlist)
                .where(Playlist.owner == owner, Playlist.followed.is_(True))
                .where((Playlist.members_refreshed_at.is_(None)) | (Playlist.members_refreshed_at < cutoff))
                .order_by(Playlist.id.asc())
            )
        ).scalars().all()
    stale = sorted(stale, key=lambda p: p.creator != uploader)  # stable: same creator first
    for pl in stale:
        try:
            async with scan_lock:
                info, ids = await fetch_member_ids(pl.url, owner)
        except Exception as exc:
            logger.debug("could not refresh playlist %r while placing %s: %s", pl.title, video_id, exc)
            continue
        await record_playlist(
            owner=owner, playlist_id=pl.playlist_id, title=info.get("title") or pl.title, url=pl.url,
            creator=info.get("uploader") or pl.creator, member_ids=ids, followed=True,
        )
        if video_id in ids:
            return await owning_playlist(owner, video_id)
    return None


def _entry_for(item: MediaItem, media_root: str, pl: Playlist) -> dict:
    media = Path(media_root) / item.local_path
    entry: dict = {}
    try:
        entry = json.loads(media.with_name(media.stem + ".info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # No sidecar: the creator folder the file sits in is the next best thing.
        entry = {"id": item.video_id, "title": item.title, "uploader": media.parent.name}
    return {**entry, "playlist_title": pl.title, "playlist_id": pl.playlist_id}


async def reconcile_playlist(pl: Playlist, media_root: str, apply: bool = True) -> dict:
    """File standalone videos that this playlist now claims into its show.

    Only files sitting in singles/ are candidates -- a file already inside a
    show is never moved (first owner wins). With `apply=False`, or for a file
    downloaded before the playlist was first recorded, nothing is touched and
    the move is only listed under "would_move".
    """
    report: dict = {"moved": [], "would_move": []}
    async with async_session() as session:
        items = (
            await session.execute(
                select(MediaItem)
                .join(PlaylistMember, PlaylistMember.video_id == MediaItem.video_id)
                .where(PlaylistMember.playlist_pk == pl.id)
                .where(MediaItem.owner == pl.owner, MediaItem.playlist_pk.is_(None))
                .where(MediaItem.local_path.like(f"{library_layout.SINGLES_SUBDIR}/%"))
            )
        ).scalars().all()
        for item in items:
            top = await owning_playlist(pl.owner, item.video_id)
            if top and top.id != pl.id:
                continue  # a higher-precedence playlist claims it; it will file it
            entry = _entry_for(item, media_root, pl)
            abs_path = str(Path(media_root) / item.local_path)
            if apply and item.created_at >= pl.created_at:
                new = library_layout.relocate(abs_path, entry, media_root)
                if new:
                    item.local_path = str(Path(new).relative_to(media_root))
                    thumb = library_layout.find_thumbnail(Path(new), "series")
                    item.thumbnail_path = str(thumb.relative_to(media_root)) if thumb else None
                    item.playlist_pk = pl.id
                    report["moved"].append(item.id)
                    logger.info("moved %r into the %r show", item.title, pl.title)
                continue
            dest = library_layout.relocate(abs_path, entry, media_root, dry_run=True)
            if dest:
                report["would_move"].append({
                    "media_id": item.id, "title": item.title, "playlist": pl.title,
                    "from": item.local_path, "to": str(Path(dest).relative_to(media_root)),
                })
        await session.commit()
    if report["would_move"]:
        logger.info(
            "%d standalone file(s) predate playlist %r and were left in place (report only)",
            len(report["would_move"]), pl.title,
        )
    return report


async def preview_backfill(owner: str, media_root: str) -> list[dict]:
    """Every standalone file some recorded playlist claims, moved or not."""
    async with async_session() as session:
        pls = (
            await session.execute(
                select(Playlist).where(Playlist.owner == owner)
                .order_by(Playlist.followed.desc(), Playlist.id.asc())
            )
        ).scalars().all()
    plan: list[dict] = []
    claimed: set[int] = set()
    for pl in pls:  # same precedence as owning_playlist: one owner per file
        for row in (await reconcile_playlist(pl, media_root, apply=False))["would_move"]:
            if row["media_id"] not in claimed:
                claimed.add(row["media_id"])
                plan.append(row)
    return plan
