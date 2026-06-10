import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from db import Source, get_session
from services.poller import poll_source, schedule_source, scheduler

router = APIRouter(prefix="/api/sources", tags=["sources"])


class SourceCreate(BaseModel):
    url: str
    label: str | None = None
    poll_interval_minutes: int = 60


class SourcePatch(BaseModel):
    label: str | None = None
    enabled: bool | None = None
    poll_interval_minutes: int | None = None


class SourceRead(BaseModel):
    id: int
    url: str
    label: str | None
    platform: str | None
    enabled: bool
    poll_interval_minutes: int
    owner: str
    last_polled_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post("", response_model=SourceRead, status_code=201)
async def create_source(
    body: SourceCreate,
    owner: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    source = Source(
        url=body.url,
        label=body.label,
        poll_interval_minutes=body.poll_interval_minutes,
        owner=owner,
    )
    session.add(source)
    await session.commit()
    await session.refresh(source)
    schedule_source(source, run_now=True)
    return source


@router.get("", response_model=list[SourceRead])
async def list_sources(
    owner: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Source).where(Source.owner == owner).order_by(Source.created_at.desc())
    )
    return result.scalars().all()


@router.patch("/{source_id}", response_model=SourceRead)
async def patch_source(
    source_id: int,
    body: SourcePatch,
    owner: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    source = await session.get(Source, source_id)
    if not source or source.owner != owner:
        raise HTTPException(status_code=404)
    if body.label is not None:
        source.label = body.label
    if body.enabled is not None:
        source.enabled = body.enabled
    if body.poll_interval_minutes is not None:
        source.poll_interval_minutes = body.poll_interval_minutes
    await session.commit()
    await session.refresh(source)
    schedule_source(source)
    return source


@router.delete("/{source_id}", status_code=204)
async def delete_source(
    source_id: int,
    owner: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    source = await session.get(Source, source_id)
    if not source or source.owner != owner:
        raise HTTPException(status_code=404)
    job_id = f"source_{source_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    await session.delete(source)
    await session.commit()


@router.post("/{source_id}/poll", status_code=202)
async def trigger_poll(
    source_id: int,
    owner: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    source = await session.get(Source, source_id)
    if not source or source.owner != owner:
        raise HTTPException(status_code=404)
    asyncio.create_task(poll_source(source_id))
    return {"detail": "poll triggered"}
