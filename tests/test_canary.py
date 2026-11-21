"""services/canary.py -- probe outcomes, cookie handling, and what reads them."""

import httpx
import pytest
from fastapi import FastAPI

from api.settings import router as settings_router
from services import canary
from services.service_status import compute_service_status

LOGIN = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t0\tLOGIN_INFO\tx\n"
    ".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\ty\n"
)


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL; `behaviour` decides what a probe sees."""

    behaviour = "data"
    seen_opts: list[dict] = []

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.seen_opts.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        if FakeYDL.behaviour == "data":
            for hook in self.opts["progress_hooks"]:
                hook({"status": "downloading", "downloaded_bytes": canary.CANARY_PROBE_BYTES})
        elif FakeYDL.behaviour == "403":
            raise Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden")
        elif FakeYDL.behaviour == "rotate":
            # yt-dlp saves the jar back to the cookies file on exit.
            with open(self.opts["cookiefile"], "w") as f:
                f.write("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\ty\n")
            for hook in self.opts["progress_hooks"]:
                hook({"status": "downloading", "downloaded_bytes": canary.CANARY_PROBE_BYTES})
        elif FakeYDL.behaviour == "silent":
            self.opts["logger"].error("ERROR: Requested format is not available")
        return {}


@pytest.fixture(autouse=True)
def _fake_ytdlp(monkeypatch):
    FakeYDL.behaviour = "data"
    FakeYDL.seen_opts = []
    monkeypatch.setattr(canary.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(canary, "CANARY_URLS", ["https://www.youtube.com/watch?v=aqz-KE-bpKQ"])
    canary._results.clear()
    yield
    canary._results.clear()


def test_probe_stops_once_data_flows(tmp_path):
    result = canary.probe("u", None, str(tmp_path))
    assert result.ok and result.bytes == canary.CANARY_PROBE_BYTES


def test_probe_classifies_a_403(tmp_path):
    FakeYDL.behaviour = "403"
    result = canary.probe("u", None, str(tmp_path))
    assert not result.ok and result.kind == "forbidden"


def test_probe_uses_logged_error_when_ytdlp_swallows_it(tmp_path):
    FakeYDL.behaviour = "silent"
    result = canary.probe("u", None, str(tmp_path))
    assert not result.ok and result.kind == "formats_missing"


def test_probe_uses_download_options_without_a_client_pin(tmp_path):
    canary.probe("u", None, str(tmp_path))
    opts = FakeYDL.seen_opts[-1]
    assert opts["format"] == "bv*+ba"
    assert opts["js_runtimes"] == {"node": {}}
    assert "extractor_args" not in opts


def test_run_once_probes_anonymously_and_per_owner(tmp_path):
    (tmp_path / "reivi.txt").write_text(LOGIN)
    results = canary.run_once(str(tmp_path))
    assert set(results) == {"anonymous", "reivi"}
    assert results["anonymous"].logged_in_before is None
    assert results["reivi"].ok and results["reivi"].logged_in_before and results["reivi"].logged_in_after


def test_run_once_never_rewrites_the_owners_file(tmp_path):
    (tmp_path / "reivi.txt").write_text(LOGIN)
    FakeYDL.behaviour = "rotate"
    results = canary.run_once(str(tmp_path))
    assert (tmp_path / "reivi.txt").read_text() == LOGIN
    # ...but the rotation itself is detected on the copy.
    assert results["reivi"].logged_in_before is True and results["reivi"].logged_in_after is False


def test_failing_kind(tmp_path):
    assert canary.failing_kind() is None
    FakeYDL.behaviour = "403"
    canary.run_once(str(tmp_path))
    assert canary.failing_kind().value == "forbidden"


async def test_failing_canary_alone_marks_service_degraded(session, tmp_path):
    FakeYDL.behaviour = "403"
    canary.run_once(str(tmp_path))
    status = await compute_service_status(session)
    assert status["degraded"] is True
    assert status["recent_failures"] == 0
    assert status["canary"]["ok"] is False and status["canary"]["kind"] == "forbidden"
    assert status["detected_since"] == status["canary"]["checked_at"]


async def test_healthy_canary_is_not_degraded(session, tmp_path):
    canary.run_once(str(tmp_path))
    status = await compute_service_status(session)
    assert status["degraded"] is False and status["canary"]["ok"] is True


@pytest.fixture
async def settings_client(tmp_path, monkeypatch):
    import api.settings as settings_module

    monkeypatch.setattr(settings_module, "COOKIES_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(settings_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_cookies_status_reports_login_and_last_check(settings_client, tmp_path):
    (tmp_path / "reivi.txt").write_text(LOGIN)
    canary.run_once(str(tmp_path))
    body = (await settings_client.get("/api/settings/cookies", headers={"Remote-User": "reivi"})).json()
    assert body["has_cookies"] and body["logged_in"] is True
    assert body["last_check"]["ok"] is True and body["last_check"]["logged_in_after"] is True


async def test_cookies_status_flags_a_file_that_is_not_a_login(settings_client, tmp_path):
    (tmp_path / "reivi.txt").write_text("# Netscape HTTP Cookie File\n")
    body = (await settings_client.get("/api/settings/cookies", headers={"Remote-User": "reivi"})).json()
    assert body["has_cookies"] and body["logged_in"] is False and body["last_check"] is None


async def test_service_status_endpoint_includes_canary(settings_client):
    body = (await settings_client.get("/api/settings/service-status")).json()
    assert body["canary"] == {"ok": None, "checked_at": None, "kind": None}
