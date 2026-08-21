"""db.py -- create_all()'s ad-hoc "add column if missing" migrations.

There's no migration tool in this project (see the comment in db.py) --
each new column added to an existing table over the project's history got
its own try/select-except/alter block, run on every startup. Untested, this
is exactly the kind of thing that silently breaks (wrong table, wrong
type, a typo'd column name) and only surfaces the next time someone
restarts a pod against a real, already-populated database.
"""

from sqlalchemy import text

from db import create_all, engine


async def test_create_all_is_idempotent():
    # The real startup path: create_all() runs on every boot, against a DB
    # that already has every column. Must not raise or duplicate anything.
    await create_all()
    await create_all()


async def test_create_all_adds_missing_columns_to_a_pre_existing_schema():
    # Simulate a deployment from before every incremental column existed:
    # only the original, minimal `download` table.
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS download"))
        await conn.execute(text("DROP TABLE IF EXISTS source"))
        await conn.execute(
            text(
                """
                CREATE TABLE download (
                    id INTEGER PRIMARY KEY,
                    url TEXT NOT NULL,
                    title TEXT,
                    platform TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    progress FLOAT NOT NULL DEFAULT 0.0,
                    error TEXT,
                    owner TEXT NOT NULL DEFAULT 'anonymous',
                    source_id INTEGER,
                    created_at TIMESTAMP,
                    finished_at TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE source (
                    id INTEGER PRIMARY KEY,
                    url TEXT NOT NULL,
                    label TEXT,
                    platform TEXT,
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    poll_interval_minutes INTEGER NOT NULL DEFAULT 60,
                    owner TEXT NOT NULL DEFAULT 'anonymous',
                    last_polled_at TIMESTAMP,
                    created_at TIMESTAMP
                )
                """
            )
        )

    await create_all()

    # Every incrementally-added column should now be queryable.
    async with engine.begin() as conn:
        await conn.execute(text("SELECT include_shorts, last_poll_error FROM source LIMIT 1"))
        await conn.execute(
            text(
                "SELECT current_index, total_entries, current_title, completed_items, "
                "retry_count, retry_at, creator, folder_hint FROM download LIMIT 1"
            )
        )


async def test_create_all_preserves_existing_rows_when_adding_columns():
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS download"))
        await conn.execute(
            text(
                """
                CREATE TABLE download (
                    id INTEGER PRIMARY KEY,
                    url TEXT NOT NULL,
                    title TEXT,
                    platform TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    progress FLOAT NOT NULL DEFAULT 0.0,
                    error TEXT,
                    owner TEXT NOT NULL DEFAULT 'anonymous',
                    source_id INTEGER,
                    created_at TIMESTAMP,
                    finished_at TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text("INSERT INTO download (url, owner) VALUES ('https://example.com/old-row', 'reivi')")
        )

    await create_all()

    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT url, owner, retry_count FROM download"))
        row = result.first()
        assert row.url == "https://example.com/old-row"
        assert row.owner == "reivi"
        assert row.retry_count == 0  # the new column's default, backfilled
