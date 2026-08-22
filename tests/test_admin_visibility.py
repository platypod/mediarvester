"""api/deps.py's is_admin + the admin bypass on every owner-scoped endpoint.

2026-08-22 feature: Authelia already forwards LDAP group membership as
Remote-Groups to every service behind forward-auth (comma-separated) --
mediarvester just wasn't reading it. A requester in any of ADMIN_GROUPS
(default {"admins"}) sees and can manage every owner's Downloads/Sources/
MediaItems; everyone else stays scoped to their own, exactly as before.

2026-08-22 follow-up: ADMIN_GROUP (single group) became ADMIN_GROUPS (a set) so
the stack can grant admin via any of mediarvester_admin/media_admin/admins,
not just one hardcoded name -- see platypod/stack's access-groups.yaml.
"""

import httpx
import pytest
from fastapi import FastAPI

from api.deps import ADMIN_GROUPS, is_admin
from api.downloads import router as downloads_router
from api.media import router as media_router
from api.sources import router as sources_router

_ADMIN_GROUP = next(iter(ADMIN_GROUPS))  # any one admin-granting group works for these tests


class _FakeRequest:
    def __init__(self, groups_header: str | None):
        self.headers = {"Remote-Groups": groups_header} if groups_header is not None else {}


def test_is_admin_true_when_group_present():
    assert is_admin(_FakeRequest(f"media,{_ADMIN_GROUP},dev")) is True


def test_is_admin_false_when_group_absent():
    assert is_admin(_FakeRequest("media,dev")) is False


def test_is_admin_false_when_header_missing():
    assert is_admin(_FakeRequest(None)) is False


def test_is_admin_tolerates_stray_whitespace_around_group_names():
    assert is_admin(_FakeRequest(f" media , {_ADMIN_GROUP} ")) is True


def test_is_admin_false_for_empty_header():
    assert is_admin(_FakeRequest("")) is False


# --- API-level admin bypass -------------------------------------------------


def _headers(user="reivi", admin=False):
    h = {"Remote-User": user}
    if admin:
        h["Remote-Groups"] = f"media,{_ADMIN_GROUP}"
    return h


@pytest.fixture
async def downloads_client():
    app = FastAPI()
    app.include_router(downloads_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def media_client():
    app = FastAPI()
    app.include_router(media_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def sources_client(monkeypatch):
    import api.sources as sources_module

    monkeypatch.setattr(sources_module, "schedule_source", lambda *a, **kw: None)

    class _FakeScheduler:
        def get_job(self, job_id):
            return None

        def remove_job(self, job_id):
            pass

    monkeypatch.setattr(sources_module, "scheduler", _FakeScheduler())

    app = FastAPI()
    app.include_router(sources_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_non_admin_still_only_sees_their_own_downloads(downloads_client, session, make_download):
    await make_download(session, url="https://example.com/mine", owner="reivi")
    await make_download(session, url="https://example.com/not-mine", owner="someone_else")
    resp = await downloads_client.get("/api/downloads", headers=_headers(admin=False))
    urls = {row["url"] for row in resp.json()}
    assert urls == {"https://example.com/mine"}


async def test_admin_sees_every_owners_downloads(downloads_client, session, make_download):
    await make_download(session, url="https://example.com/mine", owner="reivi")
    await make_download(session, url="https://example.com/not-mine", owner="someone_else")
    resp = await downloads_client.get("/api/downloads", headers=_headers(admin=True))
    urls = {row["url"] for row in resp.json()}
    assert urls == {"https://example.com/mine", "https://example.com/not-mine"}


async def test_admin_can_delete_another_owners_download(
    downloads_client, session, make_download, monkeypatch
):
    dl = await make_download(session, url="https://example.com/v1", owner="someone_else", status="done")
    resp = await downloads_client.delete(f"/api/downloads/{dl.id}", headers=_headers(admin=True))
    assert resp.status_code == 204


async def test_non_admin_still_gets_404_deleting_another_owners_download(downloads_client, session, make_download):
    dl = await make_download(session, url="https://example.com/v1", owner="someone_else")
    resp = await downloads_client.delete(f"/api/downloads/{dl.id}", headers=_headers(admin=False))
    assert resp.status_code == 404


async def test_admin_sees_every_owners_media(media_client, session, make_download, make_media_item):
    dl1 = await make_download(session, owner="reivi", status="done")
    dl2 = await make_download(session, owner="someone_else", status="done")
    await make_media_item(session, dl1.id, owner="reivi", title="Mine")
    await make_media_item(session, dl2.id, owner="someone_else", title="Not Mine")
    resp = await media_client.get("/api/media", headers=_headers(admin=True))
    titles = {row["title"] for row in resp.json()}
    assert titles == {"Mine", "Not Mine"}


async def test_non_admin_still_only_sees_their_own_media(media_client, session, make_download, make_media_item):
    dl1 = await make_download(session, owner="reivi", status="done")
    dl2 = await make_download(session, owner="someone_else", status="done")
    await make_media_item(session, dl1.id, owner="reivi", title="Mine")
    await make_media_item(session, dl2.id, owner="someone_else", title="Not Mine")
    resp = await media_client.get("/api/media", headers=_headers(admin=False))
    titles = {row["title"] for row in resp.json()}
    assert titles == {"Mine"}


async def test_admin_sees_every_owners_sources(sources_client, session, make_source):
    await make_source(session, url="https://www.youtube.com/@Mine", owner="reivi")
    await make_source(session, url="https://www.youtube.com/@NotMine", owner="someone_else")
    resp = await sources_client.get("/api/sources", headers=_headers(admin=True))
    urls = {row["url"] for row in resp.json()}
    assert urls == {"https://www.youtube.com/@Mine", "https://www.youtube.com/@NotMine"}


async def test_admin_can_patch_another_owners_source(sources_client, session, make_source):
    source = await make_source(session, owner="someone_else", label="Old")
    resp = await sources_client.patch(
        f"/api/sources/{source.id}", json={"label": "New"}, headers=_headers(admin=True)
    )
    assert resp.status_code == 200
    assert resp.json()["label"] == "New"


async def test_non_admin_still_gets_404_patching_another_owners_source(sources_client, session, make_source):
    source = await make_source(session, owner="someone_else")
    resp = await sources_client.patch(
        f"/api/sources/{source.id}", json={"label": "New"}, headers=_headers(admin=False)
    )
    assert resp.status_code == 404
