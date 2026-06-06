from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from db import MediaItem, get_session

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

    model_config = {"from_attributes": True}


@router.get("", response_model=list[MediaRead])
async def list_media(
    platform: str | None = None,
    owner: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    q = select(MediaItem).where(MediaItem.owner == owner).order_by(MediaItem.created_at.desc())
    if platform:
        q = q.where(MediaItem.platform == platform)
    result = await session.execute(q)
    return result.scalars().all()


@router.delete("/{media_id}", status_code=204)
async def delete_media(
    media_id: int,
    owner: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    item = await session.get(MediaItem, media_id)
    if not item or item.owner != owner:
        raise HTTPException(status_code=404)
    await session.delete(item)
    await session.commit()
