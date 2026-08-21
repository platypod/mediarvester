import asyncio
import json
from datetime import datetime
from logging import getLogger
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from db import Download, MediaItem, async_session, get_session
from services.downloader import downloader

logger = getLogger(__name__)

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
    current_index: int | None
    total_entries: int | None
    current_title: str | None
    completed_items: list[str] | None

    model_config = {"from_attributes": True}


def _is_probably_collection_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/").lower()
    query = parse_qs(parsed.query)

    if "list" in query:
        return True
    if path.endswith("/playlist"):
        return True
    if path.endswith(("/videos", "/shorts", "/streams")):
        return True
    if path.startswith(("/channel/", "/user/", "/c/", "/@")):
        return True
    return False


@router.post("", response_model=DownloadRead, status_code=201)
async def create_download(
    body: DownloadCreate,
    response: Response,
    owner: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    is_collection = _is_probably_collection_url(body.url)

    # Never queue the same URL twice concurrently for one owner.
    existing = (
        await session.execute(
            select(Download)
            .where(Download.owner == owner)
            .where(Download.url == body.url)
            .where(Download.status.in_(("queued", "downloading")))
            .order_by(Download.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        response.status_code = 200
        return existing

    # For single-item URLs, dedupe already-complete records.
    # For collection URLs (playlist/channel tabs), allow re-runs so users can
    # fetch entries missed during a previous partial run.
    if not is_collection:
        existing = (
            await session.execute(
                select(Download)
                .where(Download.owner == owner)
                .where(Download.url == body.url)
                .where(Download.status == "done")
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
    if not existing:
        dl = Download(url=body.url, owner=owner)
        session.add(dl)
        await session.commit()
        await session.refresh(dl)
        downloader.enqueue(dl.id, dl.url, owner)
        logger.info("download %d queued by %s: %s", dl.id, owner, dl.url)
        return dl

    response.status_code = 200
    return existing


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
    media_items = (
        await session.execute(select(MediaItem).where(MediaItem.download_id == download_id))
    ).scalars().all()
    for item in media_items:
        await session.delete(item)
    await session.flush()
    await session.delete(dl)
    await session.commit()
    logger.info("download %d removed by %s", download_id, owner)


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
            payload = {
                "progress": row.progress,
                "status": row.status,
                "current_index": row.current_index,
                "total_entries": row.total_entries,
                "current_title": row.current_title,
                "completed_items": row.completed_items,
            }
            yield f"data: {json.dumps(payload)}\n\n"
            if row.status in ("done", "error"):
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")
