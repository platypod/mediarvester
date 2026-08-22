"""services/downloader.py -- recover_interrupted / recover_missed_retries.

Both run once at startup, plugging two different restart-loses-state gaps:
a row stuck mid-download when the process died (recover_interrupted,
pre-existing), and a scheduled auto-retry that never fired because the
process died during its in-memory sleep (recover_missed_retries, added
2026-08-22 after confirming this happened for real -- a redeploy landed 3
minutes before a scheduled retry, silently dropping it with no trace).
"""

from datetime import datetime, timedelta

import services.downloader as downloader_module
from services.downloader import recover_interrupted, recover_missed_retries


async def test_recover_interrupted_reenqueues_stuck_downloading_rows(session, make_download, monkeypatch):
    dl = await make_download(session, url="https://example.com/v1", status="downloading", progress=42.0)
    enqueued = []
    monkeypatch.setattr(downloader_module.downloader, "enqueue", lambda *a, **kw: enqueued.append((a, kw)))

    await recover_interrupted()

    await session.refresh(dl)
    assert dl.status == "queued"
    assert dl.progress == 0.0
    assert len(enqueued) == 1
    args, kwargs = enqueued[0]
    assert args[0] == dl.id
    assert kwargs.get("force") is True


async def test_recover_interrupted_reenqueues_stuck_queued_rows(session, make_download, monkeypatch):
    dl = await make_download(session, url="https://example.com/v1", status="queued")
    enqueued = []
    monkeypatch.setattr(downloader_module.downloader, "enqueue", lambda *a, **kw: enqueued.append((a, kw)))
    await recover_interrupted()
    assert len(enqueued) == 1


async def test_recover_interrupted_leaves_done_and_error_rows_alone(session, make_download, monkeypatch):
    done = await make_download(session, url="https://example.com/done", status="done")
    error = await make_download(session, url="https://example.com/error", status="error")
    enqueued = []
    monkeypatch.setattr(downloader_module.downloader, "enqueue", lambda *a, **kw: enqueued.append(a))

    await recover_interrupted()

    assert enqueued == []
    await session.refresh(done)
    await session.refresh(error)
    assert done.status == "done"
    assert error.status == "error"


async def test_recover_missed_retries_refires_a_past_due_retry(session, make_download, monkeypatch):
    dl = await make_download(
        session,
        url="https://example.com/v1",
        status="error",
        retry_count=1,
        retry_at=datetime.utcnow() - timedelta(minutes=1),
        title="Some Video",
    )
    enqueued = []
    monkeypatch.setattr(downloader_module.downloader, "enqueue", lambda *a, **kw: enqueued.append(a))

    await recover_missed_retries()

    assert len(enqueued) == 1
    _, new_url, new_owner = enqueued[0]
    assert new_url == "https://example.com/v1"
    assert new_owner == dl.owner

    from db import Download
    from sqlalchemy import select

    rows = (
        await session.execute(select(Download).where(Download.url == "https://example.com/v1"))
    ).scalars().all()
    assert len(rows) == 2
    new_row = next(r for r in rows if r.id != dl.id)
    assert new_row.retry_count == 2  # dl.retry_count(1) + 1


async def test_recover_missed_retries_ignores_a_retry_not_yet_due(session, make_download, monkeypatch):
    await make_download(
        session,
        url="https://example.com/v1",
        status="error",
        retry_count=0,
        retry_at=datetime.utcnow() + timedelta(minutes=5),
    )
    enqueued = []
    monkeypatch.setattr(downloader_module.downloader, "enqueue", lambda *a, **kw: enqueued.append(a))
    await recover_missed_retries()
    assert enqueued == []


async def test_recover_missed_retries_ignores_an_exhausted_retry(session, make_download, monkeypatch):
    # retry_at is None once _MAX_AUTO_RETRIES is hit -- nothing to recover.
    await make_download(
        session, url="https://example.com/v1", status="error", retry_count=3, retry_at=None
    )
    enqueued = []
    monkeypatch.setattr(downloader_module.downloader, "enqueue", lambda *a, **kw: enqueued.append(a))
    await recover_missed_retries()
    assert enqueued == []


async def test_recover_missed_retries_skips_a_row_whose_retry_already_fired(
    session, make_download, monkeypatch
):
    # The scheduled retry actually happened (successfully or not) and
    # produced a newer row for the same url+owner -- this one is stale
    # history, not a lost retry.
    old = await make_download(
        session,
        url="https://example.com/v1",
        status="error",
        retry_count=0,
        retry_at=datetime.utcnow() - timedelta(minutes=10),
    )
    await make_download(session, url="https://example.com/v1", status="error", retry_count=1, retry_at=None)

    enqueued = []
    monkeypatch.setattr(downloader_module.downloader, "enqueue", lambda *a, **kw: enqueued.append(a))
    await recover_missed_retries()
    assert enqueued == []


async def test_recover_missed_retries_is_scoped_per_owner(session, make_download, monkeypatch):
    # A newer row for the same URL but a *different* owner must not mask a
    # genuinely lost retry for this owner.
    await make_download(
        session,
        url="https://example.com/v1",
        owner="reivi",
        status="error",
        retry_count=0,
        retry_at=datetime.utcnow() - timedelta(minutes=1),
    )
    await make_download(session, url="https://example.com/v1", owner="someone_else", status="done")

    enqueued = []
    monkeypatch.setattr(downloader_module.downloader, "enqueue", lambda *a, **kw: enqueued.append(a))
    await recover_missed_retries()
    assert len(enqueued) == 1
