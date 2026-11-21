"""services/retry_strategy.py -- retries change strategy with the error.

Covers the plan rules, that every client name is real (the "tv_embedded"
lesson), and the wiring: error -> retry row -> enqueue -> yt-dlp options.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS

import services.downloader as downloader_module
from db import Download
from services import retry_strategy
from services.downloader import Downloader, recover_interrupted, recover_missed_retries
from services.ytdlp_errors import ErrorKind

EMBED = (
    "Video unavailable. This video contains content from TF1 (TV Content), who has blocked it "
    "from display on this website or application"
)
FORBIDDEN = "ERROR: unable to download video data: HTTP Error 403: Forbidden"
SABR = "ERROR: [youtube] x: Requested format is not available. Use --list-formats"


def _all_client_names():
    names = set(retry_strategy.WITHOUT_EMBEDDED)
    for entry in retry_strategy.STREAM_FAILURE_LADDER:
        names.update(entry or [])
    return names


@pytest.mark.parametrize("name", sorted(_all_client_names()))
def test_every_client_name_is_known_to_yt_dlp(name):
    # "default" and "-<client>" are yt-dlp's own selector syntax.
    if name == "default":
        return
    assert name.lstrip("-") in INNERTUBE_CLIENTS


@pytest.mark.parametrize("error", ["Private video. Sign in if you've been granted access", "Sign in to confirm your age"])
def test_permanent_and_auth_errors_are_not_retried(error):
    plan = retry_strategy.plan(error, 1)
    assert plan.retry is False


def test_embed_block_retries_without_the_embedded_client():
    for attempt in (1, 2, 3, 4):
        plan = retry_strategy.plan(EMBED, attempt)
        assert plan.retry and plan.kind is ErrorKind.EMBED_BLOCKED
        assert plan.player_client == ["default", "-web_embedded"]


@pytest.mark.parametrize("error", [FORBIDDEN, SABR])
def test_stream_failures_walk_the_ladder_then_return_to_defaults(error):
    clients = [retry_strategy.plan(error, attempt).player_client for attempt in (1, 2, 3, 4, 5)]
    assert clients == [["tv_samsung", "visionos"], ["tv_samsung"], ["visionos"], None, None]


@pytest.mark.parametrize("error", ["HTTP Error 429: Too Many Requests", "Premieres in 2 days", "boom", None])
def test_waiting_kinds_keep_the_defaults(error):
    plan = retry_strategy.plan(error, 1)
    assert plan.retry and plan.player_client is None


def test_encode_decode_round_trip():
    assert retry_strategy.encode(None) is None
    assert retry_strategy.decode(None) is None
    assert retry_strategy.decode(retry_strategy.encode(["default", "-web_embedded"])) == ["default", "-web_embedded"]


def test_override_reaches_the_ytdlp_options():
    opts = Downloader()._build_opts(1, "o", url="https://www.youtube.com/watch?v=x", player_client=["tv_samsung"])
    assert opts["extractor_args"] == {"youtube": {"player_client": ["tv_samsung"]}}


async def test_on_error_schedules_the_planned_client(session, make_download, monkeypatch):
    dl = await make_download(session, url="https://www.youtube.com/watch?v=e", status="downloading")
    downloader = Downloader()
    captured = {}
    monkeypatch.setattr(downloader, "_schedule_retries", lambda *a, **kw: captured.update(kw))
    await downloader._on_error(dl.id, EMBED)
    await session.refresh(dl)
    assert dl.retry_at is not None
    assert captured["player_client"] == ["default", "-web_embedded"]
    assert captured["kind"] == "embed_blocked"


async def test_on_error_gives_up_at_once_on_a_permanent_error(session, make_download, monkeypatch):
    dl = await make_download(session, url="https://www.youtube.com/watch?v=p", status="downloading")
    downloader = Downloader()
    called = []
    monkeypatch.setattr(downloader, "_schedule_retries", lambda *a, **kw: called.append(a))
    await downloader._on_error(dl.id, "Video unavailable. This video has been removed by the uploader")
    await session.refresh(dl)
    assert called == []
    # retry_at None is what the UI reads as "gave up, resubmit if you want it".
    assert dl.status == "error" and dl.retry_at is None


async def test_retry_row_persists_and_enqueues_the_override(session, monkeypatch):
    downloader = Downloader()
    enqueued = []
    monkeypatch.setattr(downloader, "enqueue", lambda *a, **kw: enqueued.append(kw))
    await downloader._retry_after_delay("https://www.youtube.com/watch?v=r", "reivi", None, 1, 0, player_client=["tv_samsung", "visionos"])
    row = (await session.execute(select(Download).where(Download.url == "https://www.youtube.com/watch?v=r"))).scalar_one()
    assert row.player_client == "tv_samsung,visionos"
    assert enqueued[0]["player_client"] == ["tv_samsung", "visionos"]


async def test_recover_interrupted_keeps_the_override(session, make_download, monkeypatch):
    await make_download(session, url="https://www.youtube.com/watch?v=i", status="downloading", player_client="tv_samsung")
    enqueued = []
    monkeypatch.setattr(downloader_module.downloader, "enqueue", lambda *a, **kw: enqueued.append(kw))
    await recover_interrupted()
    assert enqueued[0]["player_client"] == ["tv_samsung"]


async def test_missed_retry_applies_the_plan(session, make_download, monkeypatch):
    await make_download(
        session, url="https://www.youtube.com/watch?v=m", status="error", error=FORBIDDEN,
        retry_count=0, retry_at=datetime.utcnow() - timedelta(minutes=1),
    )
    enqueued = []
    monkeypatch.setattr(downloader_module.downloader, "enqueue", lambda *a, **kw: enqueued.append(kw))
    await recover_missed_retries()
    assert enqueued[0]["player_client"] == ["tv_samsung", "visionos"]
    row = (await session.execute(select(Download).where(Download.retry_count == 1))).scalar_one()
    assert row.player_client == "tv_samsung,visionos"


async def test_missed_retry_of_a_permanent_error_is_dropped(session, make_download, monkeypatch):
    await make_download(
        session, url="https://www.youtube.com/watch?v=g", status="error", error="Private video",
        retry_count=0, retry_at=datetime.utcnow() - timedelta(minutes=1),
    )
    enqueued = []
    monkeypatch.setattr(downloader_module.downloader, "enqueue", lambda *a, **kw: enqueued.append(kw))
    await recover_missed_retries()
    assert enqueued == []
