import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from db import Download, MediaItem, async_session, get_session
from services.downloader import downloader

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


class DownloadCreate(BaseModel):
    url: str


class DownloadRead(BaseModel):
    id: int
    url: str
    title: str | None
    platform: str | None
    status: str
    progress: float
    error: str | None
    owner: str
    source_id: int | None
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


@router.post("", response_model=DownloadRead, status_code=201)
async def create_download(
    body: DownloadCreate,
    response: Response,
    owner: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    # Already downloaded, or already in flight — return that instead of
    # enqueuing a duplicate yt-dlp job and a duplicate MediaItem row.
    existing = (
        await session.execute(
            select(Download)
            .where(Download.owner == owner)
            .where(Download.url == body.url)
            .where(Download.status.in_(("queued", "downloading", "done")))
            .order_by(Download.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not existing:
        existing = (
            await session.execute(
                select(Download)
                .join(MediaItem, MediaItem.download_id == Download.id)
                .where(Download.owner == owner)
                .where(MediaItem.source_url == body.url)
                .order_by(Download.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if existing:
        response.status_code = 200
        return existing

    dl = Download(url=body.url, owner=owner)
    session.add(dl)
    await session.commit()
    await session.refresh(dl)
    downloader.enqueue(dl.id, dl.url, owner)
    return dl


@router.get("", response_model=list[DownloadRead])
async def list_downloads(
    status: str | None = None,
    owner: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    q = select(Download).where(Download.owner == owner).order_by(Download.created_at.desc())
    if status:
        q = q.where(Download.status.in_(status.split(",")))
    result = await session.execute(q)
    return result.scalars().all()


@router.delete("/{download_id}", status_code=204)
async def delete_download(
    download_id: int,
    owner: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    dl = await session.get(Download, download_id)
    if not dl or dl.owner != owner:
        raise HTTPException(status_code=404)
    await session.delete(dl)
    await session.commit()


@router.get("/{download_id}/progress")
async def download_progress(
    download_id: int,
    owner: str = Depends(get_current_user),
):
    async def stream():
        while True:
            async with async_session() as session:
                row = await session.get(Download, download_id)
            if not row or row.owner != owner:
                break
            yield f"data: {{\"progress\":{row.progress:.1f},\"status\":\"{row.status}\"}}\n\n"
            if row.status in ("done", "error"):
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")
