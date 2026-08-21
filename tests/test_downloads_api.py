"""api/downloads.py -- dedupe policy, owner isolation, delete cascade.

Tests the router directly (not the full app/lifespan) against the real test
DB. downloader.enqueue is monkeypatched to a recorder throughout -- these
are API/DB-contract tests, not download-execution tests (see
test_download_lifecycle.py for that).
"""

import httpx
import pytest
from fastapi import FastAPI

import api.downloads as downloads_module
from api.downloads import router as downloads_router


@pytest.fixture
def enqueued(monkeypatch):
    calls = []
    monkeypatch.setattr(downloads_module.downloader, "enqueue", lambda *a, **kw: calls.append((a, kw)))
    return calls


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(downloads_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _headers(user="reivi"):
    return {"Remote-User": user}


async def test_create_download_enqueues_and_returns_201(client, enqueued):
    resp = await client.post("/api/downloads", json={"url": "https://example.com/v1"}, headers=_headers())
    assert resp.status_code == 201
    body = resp.json()
    assert body["url"] == "https://example.com/v1"
    assert body["owner"] == "reivi"
    assert body["status"] == "queued"
    assert len(enqueued) == 1


async def test_resubmitting_a_queued_url_returns_200_and_does_not_enqueue_again(client, enqueued):
    await client.post("/api/downloads", json={"url": "https://example.com/v1"}, headers=_headers())
    resp = await client.post("/api/downloads", json={"url": "https://example.com/v1"}, headers=_headers())
    assert resp.status_code == 200
    assert len(enqueued) == 1  # still just the first one


async def test_resubmitting_a_completed_single_video_dedupes(client, enqueued, session, make_download):
    await make_download(session, url="https://example.com/done-video", owner="reivi", status="done")
    resp = await client.post(
        "/api/downloads", json={"url": "https://example.com/done-video"}, headers=_headers()
    )
    assert resp.status_code == 200
    assert enqueued == []


async def test_resubmitting_a_completed_collection_url_is_allowed_to_rerun(
    client, enqueued, session, make_download
):
    # Unlike a single video, a playlist/channel URL is allowed to re-run so
    # users can fetch entries missed during a previous partial run.
    await make_download(session, url="https://youtube.com/playlist?list=PL1", owner="reivi", status="done")
    resp = await client.post(
        "/api/downloads", json={"url": "https://youtube.com/playlist?list=PL1"}, headers=_headers()
    )
    assert resp.status_code == 201
    assert len(enqueued) == 1


async def test_dedup_via_media_item_source_url_when_download_url_differs(
    client, enqueued, session, make_download, make_media_item
):
    # A MediaItem's source_url can differ from the original Download.url
    # (e.g. a resolved redirect) -- the join-based dedupe check should still
    # catch it for a single-video URL.
    dl = await make_download(session, url="https://example.com/original", owner="reivi", status="done")
    await make_media_item(session, dl.id, source_url="https://example.com/resolved", owner="reivi")

    resp = await client.post(
        "/api/downloads", json={"url": "https://example.com/resolved"}, headers=_headers()
    )
    assert resp.status_code == 200
    assert enqueued == []


async def test_dedup_is_scoped_per_owner(client, enqueued, session, make_download):
    # The same URL, already done for a *different* owner, should not dedupe
    # -- every owner has their own independent library.
    await make_download(session, url="https://example.com/v1", owner="someone_else", status="done")
    resp = await client.post("/api/downloads", json={"url": "https://example.com/v1"}, headers=_headers())
    assert resp.status_code == 201
    assert len(enqueued) == 1


async def test_list_downloads_only_returns_the_requesting_owners_rows(client, session, make_download):
    await make_download(session, url="https://example.com/mine", owner="reivi")
    await make_download(session, url="https://example.com/not-mine", owner="someone_else")
    resp = await client.get("/api/downloads", headers=_headers())
    assert resp.status_code == 200
    urls = {row["url"] for row in resp.json()}
    assert urls == {"https://example.com/mine"}


async def test_list_downloads_filters_by_status(client, session, make_download):
    await make_download(session, url="https://example.com/done", owner="reivi", status="done")
    await make_download(session, url="https://example.com/queued", owner="reivi", status="queued")
    resp = await client.get("/api/downloads", params={"status": "done"}, headers=_headers())
    urls = {row["url"] for row in resp.json()}
    assert urls == {"https://example.com/done"}


async def test_delete_removes_download_and_its_media_items(client, session, make_download, make_media_item):
    dl = await make_download(session, url="https://example.com/v1", owner="reivi", status="done")
    await make_media_item(session, dl.id, owner="reivi")

    resp = await client.delete(f"/api/downloads/{dl.id}", headers=_headers())
    assert resp.status_code == 204

    from db import Download, MediaItem
    from sqlalchemy import select

    # .get() would return the identity-mapped Python object already cached
    # by this session from make_download() above without re-querying --
    # use an explicit SELECT so a truly-deleted row comes back empty.
    remaining_download = (
        await session.execute(select(Download).where(Download.id == dl.id))
    ).scalar_one_or_none()
    assert remaining_download is None
    remaining_items = (
        await session.execute(select(MediaItem).where(MediaItem.download_id == dl.id))
    ).scalars().all()
    assert remaining_items == []


async def test_delete_is_404_for_another_owners_download(client, session, make_download):
    dl = await make_download(session, url="https://example.com/v1", owner="someone_else")
    resp = await client.delete(f"/api/downloads/{dl.id}", headers=_headers())
    assert resp.status_code == 404


async def test_delete_is_404_for_a_nonexistent_download(client):
    resp = await client.delete("/api/downloads/999999", headers=_headers())
    assert resp.status_code == 404
