from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, is_admin
from db import MediaItem, get_session
from services import jellyfin, playlists
from services.downloader import MEDIA_ROOT

router = APIRouter(prefix="/api/media", tags=["media"])


class MediaRead(BaseModel):
    id: int
    title: str
    platform: str | None
    source_url: str
    local_path: str
    thumbnail_path: str | None
    duration_seconds: int | None
    owner: str
    download_id: int
    width: int | None
    height: int | None
    vcodec: str | None
    acodec: str | None
    abr: float | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[MediaRead])
async def list_media(
    platform: str | None = None,
    owner: str = Depends(get_current_user),
    admin: bool = Depends(is_admin),
    session: AsyncSession = Depends(get_session),
):
    q = select(MediaItem).order_by(MediaItem.created_at.desc())
    if not admin:
        q = q.where(MediaItem.owner == owner)
    if platform:
        q = q.where(MediaItem.platform == platform)
    result = await session.execute(q)
    return result.scalars().all()


@router.get("/reconcile-preview")
async def reconcile_preview(owner: str = Depends(get_current_user)):
    """Report-only: standalone files in singles/ that one of this owner's
    playlists now claims. Nothing is moved -- files downloaded before their
    playlist was recorded stay put until the owner opts in."""
    return await playlists.preview_backfill(owner, MEDIA_ROOT)


class JellyfinLink(BaseModel):
    url: str | None


@router.get("/{media_id}/jellyfin", response_model=JellyfinLink)
async def jellyfin_link(
    media_id: int,
    owner: str = Depends(get_current_user),
    admin: bool = Depends(is_admin),
    session: AsyncSession = Depends(get_session),
):
    """Deep link to this file's page on Jellyfin; `url` is null while Jellyfin
    hasn't indexed it (a scan of the path has then been requested)."""
    item = await session.get(MediaItem, media_id)
    if not item or (item.owner != owner and not admin):
        raise HTTPException(status_code=404)
    return {"url": await jellyfin.item_url(item.title, item.local_path)}


@router.delete("/{media_id}", status_code=204)
async def delete_media(
    media_id: int,
    owner: str = Depends(get_current_user),
    admin: bool = Depends(is_admin),
    session: AsyncSession = Depends(get_session),
):
    item = await session.get(MediaItem, media_id)
    if not item or (item.owner != owner and not admin):
        raise HTTPException(status_code=404)
    await session.delete(item)
    await session.commit()
