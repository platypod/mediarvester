"""api/sources.py -- owner isolation on patch/delete."""

import httpx
import pytest
from fastapi import FastAPI

import api.sources as sources_module
from api.sources import router as sources_router


@pytest.fixture(autouse=True)
def _no_real_scheduling(monkeypatch):
    # create/patch/delete all touch the real global APScheduler otherwise --
    # keep these tests scoped to the DB/HTTP contract.
    monkeypatch.setattr(sources_module, "schedule_source", lambda *a, **kw: None)

    class _FakeScheduler:
        def get_job(self, job_id):
            return None

        def remove_job(self, job_id):
            pass

    monkeypatch.setattr(sources_module, "scheduler", _FakeScheduler())


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(sources_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _headers(user="reivi"):
    return {"Remote-User": user}


async def test_create_source_defaults(client):
    resp = await client.post(
        "/api/sources", json={"url": "https://www.youtube.com/@Creator"}, headers=_headers()
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["owner"] == "reivi"
    assert body["enabled"] is True
    assert body["poll_interval_minutes"] == 60
    assert body["include_shorts"] is False


async def test_list_sources_only_returns_the_requesting_owners_rows(client, session, make_source):
    await make_source(session, url="https://www.youtube.com/@Mine", owner="reivi")
    await make_source(session, url="https://www.youtube.com/@NotMine", owner="someone_else")
    resp = await client.get("/api/sources", headers=_headers())
    urls = {row["url"] for row in resp.json()}
    assert urls == {"https://www.youtube.com/@Mine"}


async def test_patch_is_404_for_another_owners_source(client, session, make_source):
    source = await make_source(session, owner="someone_else")
    resp = await client.patch(f"/api/sources/{source.id}", json={"enabled": False}, headers=_headers())
    assert resp.status_code == 404


async def test_patch_updates_only_provided_fields(client, session, make_source):
    source = await make_source(session, owner="reivi", label="Old Label", poll_interval_minutes=60)
    resp = await client.patch(
        f"/api/sources/{source.id}", json={"poll_interval_minutes": 30}, headers=_headers()
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["poll_interval_minutes"] == 30
    assert body["label"] == "Old Label"  # untouched


async def test_delete_is_404_for_another_owners_source(client, session, make_source):
    source = await make_source(session, owner="someone_else")
    resp = await client.delete(f"/api/sources/{source.id}", headers=_headers())
    assert resp.status_code == 404


async def test_delete_removes_the_source(client, session, make_source):
    source = await make_source(session, owner="reivi")
    resp = await client.delete(f"/api/sources/{source.id}", headers=_headers())
    assert resp.status_code == 204

    from db import Source
    from sqlalchemy import select

    remaining = (await session.execute(select(Source).where(Source.id == source.id))).scalar_one_or_none()
    assert remaining is None
