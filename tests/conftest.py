import os
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

# The app runs with --app-dir src (see Dockerfile) -- every internal import
# (db, api.*, services.*) resolves relative to that directory, not the repo
# root. Mirror that here so tests import the real modules the same way.
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

# Must be set before `db` (or anything importing it) is imported anywhere,
# since db.py creates its engine at module-import time from this env var.
# A real temp file (not sqlite's :memory:) avoids the multi-connection
# in-memory-DB-per-connection pitfall with aiosqlite's pool, and matches how
# dev actually runs (a real sqlite file), not a synthetic-only setup.
_tmp_db = tempfile.NamedTemporaryFile(prefix="mediarvester-test-", suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp_db.name}"
os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "")
os.environ.setdefault("PYROSCOPE_SERVER_ADDRESS", "")

from db import Base, Download, MediaItem, Source, async_session, engine  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _clean_db():
    """Fresh schema for every test -- cheap enough at this scale, and avoids
    any cross-test bleed from leftover rows (several of the behaviours under
    test here are specifically about what *other* rows exist, e.g. the
    stale-error-row cleanup and playlist-matching candidate queries)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def session():
    async with async_session() as s:
        yield s


@pytest.fixture
def make_download():
    """Insert a Download row with sane defaults, overridable per test."""

    async def _make(session, **kwargs) -> Download:
        defaults = dict(url="https://example.com/video", owner="reivi", status="queued")
        defaults.update(kwargs)
        dl = Download(**defaults)
        session.add(dl)
        await session.commit()
        await session.refresh(dl)
        return dl

    return _make


@pytest.fixture
def make_media_item():
    async def _make(session, download_id: int, **kwargs) -> MediaItem:
        defaults = dict(
            title="Some Video",
            source_url="https://example.com/video",
            local_path="Unsorted/Some Video.mp4",
            owner="reivi",
            download_id=download_id,
        )
        defaults.update(kwargs)
        item = MediaItem(**defaults)
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return item

    return _make


@pytest.fixture
def make_source():
    async def _make(session, **kwargs) -> Source:
        defaults = dict(url="https://www.youtube.com/@SomeCreator", owner="reivi", enabled=True)
        defaults.update(kwargs)
        source = Source(**defaults)
        session.add(source)
        await session.commit()
        await session.refresh(source)
        return source

    return _make
