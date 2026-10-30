"""services/jellyfin.py + GET /api/media/{id}/jellyfin -- deep link to Jellyfin.

Jellyfin itself is mocked at the `_request` seam; what matters here is the path
matching (Jellyfin's spelling of MEDIA_ROOT, unicode normalisation) and that a
miss asks Jellyfin to scan the file instead of failing silently.
"""

import unicodedata

import httpx
import pytest
from fastapi import FastAPI

import services.jellyfin as jellyfin
from api.media import router as media_router
from api.settings import router as settings_router


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("JELLYFIN_URL", "http://jellyfin:8096")
    monkeypatch.setenv("JELLYFIN_API_KEY", "k")
    monkeypatch.setenv("JELLYFIN_PUBLIC_URL", "https://jellyfin.example.com/")
    monkeypatch.setenv("JELLYFIN_MEDIA_ROOT", "/Users/x/synology/replays")


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(method, url, key, body=None):
        seen.append((method, url, body))
        return fake.response

    fake.response = {"Items": []}
    monkeypatch.setattr(jellyfin, "_request", fake)
    fake.seen = seen
    return fake


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(media_router)
    app.include_router(settings_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def test_disabled_until_fully_configured(monkeypatch):
    for var in ("JELLYFIN_URL", "JELLYFIN_API_KEY", "JELLYFIN_PUBLIC_URL", "JELLYFIN_MEDIA_ROOT"):
        monkeypatch.delenv(var, raising=False)
    assert not jellyfin.enabled()
    monkeypatch.setenv("JELLYFIN_URL", "http://jellyfin:8096")
    assert not jellyfin.enabled()


async def test_item_url_matches_the_hit_whose_path_is_the_file(configured, calls):
    calls.response = {
        "Items": [
            {"Id": "wrong", "Path": "/Users/x/synology/replays/singles/Other/Same Title.webm"},
            {"Id": "right", "Path": "/Users/x/synology/replays/singles/Mynthos/Same Title.webm"},
        ]
    }
    url = await jellyfin.item_url("Same Title", "singles/Mynthos/Same Title.webm")
    assert url == "https://jellyfin.example.com/web/#/details?id=right"
    assert [c[0] for c in calls.seen] == ["GET"]
    assert "SearchTerm=Same+Title" in calls.seen[0][1]


async def test_item_url_ignores_unicode_normalisation_differences(configured, calls):
    nfd = unicodedata.normalize("NFD", "singles/Mynthos/AVEUGLEMAN EN CORÉE.webm")
    calls.response = {"Items": [{"Id": "id1", "Path": f"/Users/x/synology/replays/{nfd}"}]}
    url = await jellyfin.item_url("AVEUGLEMAN EN CORÉE.", "singles/Mynthos/AVEUGLEMAN EN CORÉE.webm")
    assert url.endswith("id=id1")


async def test_a_miss_asks_jellyfin_to_scan_the_file(configured, calls):
    assert await jellyfin.item_url("T", "singles/A/T.webm") is None
    method, url, body = calls.seen[-1]
    assert (method, url) == ("POST", "http://jellyfin:8096/Library/Media/Updated")
    assert body == {"Updates": [{"Path": "/Users/x/synology/replays/singles/A/T.webm", "UpdateType": "Created"}]}


async def test_jellyfin_errors_degrade_to_no_link(configured, monkeypatch):
    def boom(*a, **kw):
        raise OSError("down")

    monkeypatch.setattr(jellyfin, "_request", boom)
    assert await jellyfin.item_url("T", "singles/A/T.webm") is None


async def test_item_url_is_none_when_disabled(monkeypatch, calls):
    monkeypatch.delenv("JELLYFIN_URL", raising=False)
    assert await jellyfin.item_url("T", "p") is None
    assert calls.seen == []


async def test_endpoint_returns_the_link_for_any_owner_of_the_item(
    session, make_download, make_media_item, client, configured, calls
):
    dl = await make_download(session, owner="reivi", status="done")
    item = await make_media_item(session, dl.id, owner="reivi", title="T", local_path="singles/A/T.webm")
    calls.response = {"Items": [{"Id": "abc", "Path": "/Users/x/synology/replays/singles/A/T.webm"}]}

    resp = await client.get(f"/api/media/{item.id}/jellyfin", headers={"Remote-User": "reivi"})

    assert resp.status_code == 200
    assert resp.json() == {"url": "https://jellyfin.example.com/web/#/details?id=abc"}


async def test_endpoint_is_404_for_another_owners_item(session, make_download, make_media_item, client, configured, calls):
    dl = await make_download(session, owner="someone_else", status="done")
    item = await make_media_item(session, dl.id, owner="someone_else")
    resp = await client.get(f"/api/media/{item.id}/jellyfin", headers={"Remote-User": "reivi"})
    assert resp.status_code == 404
    assert calls.seen == []


async def test_me_reports_whether_jellyfin_is_enabled(client, configured):
    resp = await client.get("/api/settings/me", headers={"Remote-User": "reivi"})
    assert resp.json()["jellyfin_enabled"] is True
