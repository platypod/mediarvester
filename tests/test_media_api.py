"""api/media.py -- owner isolation and platform filtering."""

import httpx
import pytest
from fastapi import FastAPI

from api.media import router as media_router


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(media_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _headers(user="reivi"):
    return {"Remote-User": user}


async def test_list_media_only_returns_the_requesting_owners_items(
    session, make_download, make_media_item, client
):
    dl1 = await make_download(session, owner="reivi", status="done")
    dl2 = await make_download(session, owner="someone_else", status="done")
    await make_media_item(session, dl1.id, owner="reivi", title="Mine")
    await make_media_item(session, dl2.id, owner="someone_else", title="Not Mine")

    resp = await client.get("/api/media", headers=_headers())
    titles = {row["title"] for row in resp.json()}
    assert titles == {"Mine"}


async def test_list_media_filters_by_platform(session, make_download, make_media_item, client):
    dl = await make_download(session, owner="reivi", status="done")
    await make_media_item(session, dl.id, owner="reivi", title="YouTube video", platform="youtube")
    await make_media_item(session, dl.id, owner="reivi", title="TikTok video", platform="tiktok")

    resp = await client.get("/api/media", params={"platform": "tiktok"}, headers=_headers())
    titles = {row["title"] for row in resp.json()}
    assert titles == {"TikTok video"}


async def test_delete_is_404_for_another_owners_media_item(session, make_download, make_media_item, client):
    dl = await make_download(session, owner="someone_else", status="done")
    item = await make_media_item(session, dl.id, owner="someone_else")
    resp = await client.delete(f"/api/media/{item.id}", headers=_headers())
    assert resp.status_code == 404


async def test_delete_removes_the_media_item(session, make_download, make_media_item, client):
    dl = await make_download(session, owner="reivi", status="done")
    item = await make_media_item(session, dl.id, owner="reivi")
    resp = await client.delete(f"/api/media/{item.id}", headers=_headers())
    assert resp.status_code == 204

    from db import MediaItem
    from sqlalchemy import select

    remaining = (
        await session.execute(select(MediaItem).where(MediaItem.id == item.id))
    ).scalar_one_or_none()
    assert remaining is None
