"""GET /api/downloads -- `media_id`, so the Queue can link a finished download to its library item."""

import httpx
from fastapi import FastAPI

from api.downloads import router as downloads_router


async def _list():
    app = FastAPI()
    app.include_router(downloads_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        return {d["id"]: d for d in (await c.get("/api/downloads", headers={"Remote-User": "reivi"})).json()}


async def test_a_done_download_with_one_item_carries_its_media_id(session, make_download, make_media_item):
    dl = await make_download(session, status="done")
    item = await make_media_item(session, dl.id)
    assert (await _list())[dl.id]["media_id"] == item.id


async def test_a_collection_download_has_no_single_item_to_link(session, make_download, make_media_item):
    dl = await make_download(session, status="done")
    await make_media_item(session, dl.id, local_path="a/1.mp4")
    await make_media_item(session, dl.id, local_path="a/2.mp4")
    assert (await _list())[dl.id]["media_id"] is None


async def test_an_unfinished_download_has_no_media_id(session, make_download):
    dl = await make_download(session, status="queued")
    assert (await _list())[dl.id]["media_id"] is None
