"""Download.started_at -- when a worker actually picked the download up.

created_at is when it was queued, which includes waiting behind other
downloads, so it cannot give a duration. The Queue view shows start time and
duration from started_at / finished_at.
"""

from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI

import services.downloader as downloader_module
from api.downloads import router as downloads_router
from services.downloader import Downloader, recover_interrupted


async def test_mark_started_records_when_work_began(session, make_download):
    dl = await make_download(session)
    assert dl.started_at is None
    before = datetime.utcnow()
    await Downloader()._mark_started(dl.id)
    await session.refresh(dl)
    assert before <= dl.started_at <= datetime.utcnow()


async def test_a_recovered_download_starts_its_clock_over(session, make_download, monkeypatch):
    dl = await make_download(
        session, status="downloading", started_at=datetime.utcnow() - timedelta(hours=1)
    )
    monkeypatch.setattr(downloader_module.downloader, "enqueue", lambda *a, **kw: None)
    await recover_interrupted()
    await session.refresh(dl)
    assert dl.started_at is None


async def test_api_exposes_started_at(session, make_download):
    started = datetime(2026, 10, 24, 10, 0, 0)
    await make_download(session, status="done", started_at=started, finished_at=started + timedelta(seconds=75))
    app = FastAPI()
    app.include_router(downloads_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/api/downloads", headers={"Remote-User": "reivi"})).json()
    assert body[0]["started_at"] == "2026-10-24T10:00:00"
    assert body[0]["finished_at"] == "2026-10-24T10:01:15"
