from datetime import datetime
from os import environ

from sqlalchemy import JSON, ForeignKey, UniqueConstraint, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DATABASE_URL = environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./data/mediarvester.db",
)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Download(Base):
    __tablename__ = "download"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str]
    title: Mapped[str | None]
    platform: Mapped[str | None]
    # Same uploader/channel/creator fallback chain used for the outtmpl
    # folder name (services/downloader.py) -- populated on success, kept as
    # its own column (rather than parsed back out of local_path) so it's
    # filterable/queryable without depending on on-disk layout staying put.
    creator: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="queued")  # queued|downloading|done|error
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None]
    owner: Mapped[str] = mapped_column(default="anonymous", server_default="anonymous")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    finished_at: Mapped[datetime | None]
    # Playlist/channel downloads only -- populated from yt-dlp's per-entry
    # progress hook while a collection is in flight, so the UI can show
    # "N of M" instead of a single progress bar that resets per video.
    current_index: Mapped[int | None]
    total_entries: Mapped[int | None]
    current_title: Mapped[str | None]
    completed_items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # How many times this URL has already been auto-retried after a failure
    # (see services/downloader.py's _schedule_retries). 0 for a fresh,
    # user- or poller-initiated download; each auto-retry spawns a new
    # Download row with this incremented, so retries stay visible/queryable
    # in their own right instead of mutating history in place.
    retry_count: Mapped[int] = mapped_column(default=0, server_default="0")
    # Set on a failed row exactly when an auto-retry has actually been
    # scheduled for it (i.e. retry_count hadn't yet hit the cap) -- lets the
    # UI say "will retry automatically around HH:MM" instead of leaving a
    # bare "error" that looks like it needs the user to do something. None
    # (rather than some fixed cutoff) is what distinguishes "still being
    # handled" from "gave up for good, resubmit manually if you want it".
    retry_at: Mapped[datetime | None]
    # Set by the poller (services/poller.py) when a newly-discovered video
    # from a followed creator matches an already-downloaded playlist --
    # overrides the outtmpl's playlist-folder segment so it lands alongside
    # that playlist's other episodes instead of loose in the creator's root.
    # Persisted (not just passed at enqueue time) so a restart's
    # recover_interrupted can honour it on re-enqueue too.
    folder_hint: Mapped[str | None]
    # Video/audio profile, populated as soon as yt-dlp's progress hook sees
    # each selected format (before the file finishes) -- not just on success,
    # unlike MediaItem's copy of these same fields, so the Queue view can
    # show quality info on a still-downloading row. See services/downloader.py's
    # _profile_from_info.
    width: Mapped[int | None]
    height: Mapped[int | None]
    vcodec: Mapped[str | None]
    acodec: Mapped[str | None]
    abr: Mapped[float | None]


class Source(Base):
    __tablename__ = "source"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str]
    label: Mapped[str | None]
    platform: Mapped[str | None]
    enabled: Mapped[bool] = mapped_column(default=True)
    include_shorts: Mapped[bool] = mapped_column(default=False, server_default="0")
    poll_interval_minutes: Mapped[int] = mapped_column(default=60)
    owner: Mapped[str] = mapped_column(default="anonymous", server_default="anonymous")
    last_polled_at: Mapped[datetime | None]
    # Set whenever a poll's discovery scan gets cut short by errors (rather
    # than cleanly reaching the follow-date cutoff) -- None means the most
    # recent poll was clean. A source can silently miss new uploads for weeks
    # if every poll degrades the same way (e.g. stale cookies) with nothing
    # to show for it beyond a log line in a pod that gets recycled -- this is
    # the persistent, queryable signal that was missing.
    last_poll_error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class MediaItem(Base):
    __tablename__ = "media_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    platform: Mapped[str | None]
    source_url: Mapped[str]
    local_path: Mapped[str]
    thumbnail_path: Mapped[str | None]
    duration_seconds: Mapped[int | None]
    owner: Mapped[str] = mapped_column(default="anonymous", server_default="anonymous")
    download_id: Mapped[int] = mapped_column(ForeignKey("download.id"))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    # Video/audio profile of the downloaded file, read off yt-dlp's own
    # post-extraction info (services/downloader.py's _extract_media_profile) --
    # not re-probed from the file, so a format yt-dlp doesn't report on (rare)
    # just leaves these None rather than failing the download.
    width: Mapped[int | None]
    height: Mapped[int | None]
    vcodec: Mapped[str | None]
    acodec: Mapped[str | None]
    abr: Mapped[float | None]
    # YouTube video id, parsed from source_url (backfilled for old rows in
    # create_all) -- the key playlist membership is matched on.
    video_id: Mapped[str | None] = mapped_column(index=True)
    # The Playlist (row id, not YouTube's list id) whose show this file was
    # filed into, if any. Set means the file is owned: reconciliation never
    # moves it again.
    playlist_pk: Mapped[int | None] = mapped_column(ForeignKey("playlist.id"), nullable=True)


class Playlist(Base):
    """A playlist this owner follows or has downloaded in full, recorded
    locally so a video fetched on its own can still be filed into the
    playlist's show. See services/playlists.py."""

    __tablename__ = "playlist"
    __table_args__ = (UniqueConstraint("owner", "playlist_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(default="anonymous", server_default="anonymous")
    playlist_id: Mapped[str]  # YouTube's `list=` id
    title: Mapped[str]
    url: Mapped[str]
    creator: Mapped[str | None]
    # A followed playlist (a Source) outranks one merely downloaded once.
    followed: Mapped[bool] = mapped_column(default=False, server_default="0")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source.id"), nullable=True)
    members_refreshed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class PlaylistMember(Base):
    __tablename__ = "playlist_member"
    __table_args__ = (UniqueConstraint("playlist_pk", "video_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    playlist_pk: Mapped[int] = mapped_column(ForeignKey("playlist.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[str] = mapped_column(index=True)


async def get_session():
    async with async_session() as session:
        yield session


async def create_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # create_all() only creates missing tables; existing deployments need this
    # new column added in-place since there is no migration tool in this project.
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT include_shorts FROM source LIMIT 1"))
    except (OperationalError, ProgrammingError):
        async with engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE source ADD COLUMN include_shorts BOOLEAN DEFAULT FALSE")
            )

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT last_poll_error FROM source LIMIT 1"))
    except (OperationalError, ProgrammingError):
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE source ADD COLUMN last_poll_error TEXT"))

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT current_index FROM download LIMIT 1"))
    except (OperationalError, ProgrammingError):
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE download ADD COLUMN current_index INTEGER"))
            await conn.execute(text("ALTER TABLE download ADD COLUMN total_entries INTEGER"))
            await conn.execute(text("ALTER TABLE download ADD COLUMN current_title TEXT"))
            await conn.execute(text("ALTER TABLE download ADD COLUMN completed_items JSON"))

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT retry_count FROM download LIMIT 1"))
    except (OperationalError, ProgrammingError):
        async with engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE download ADD COLUMN retry_count INTEGER DEFAULT 0")
            )

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT retry_at FROM download LIMIT 1"))
    except (OperationalError, ProgrammingError):
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE download ADD COLUMN retry_at TIMESTAMP"))

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT creator FROM download LIMIT 1"))
    except (OperationalError, ProgrammingError):
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE download ADD COLUMN creator TEXT"))

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT folder_hint FROM download LIMIT 1"))
    except (OperationalError, ProgrammingError):
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE download ADD COLUMN folder_hint TEXT"))

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT width FROM media_item LIMIT 1"))
    except (OperationalError, ProgrammingError):
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE media_item ADD COLUMN width INTEGER"))
            await conn.execute(text("ALTER TABLE media_item ADD COLUMN height INTEGER"))
            await conn.execute(text("ALTER TABLE media_item ADD COLUMN vcodec TEXT"))
            await conn.execute(text("ALTER TABLE media_item ADD COLUMN acodec TEXT"))
            await conn.execute(text("ALTER TABLE media_item ADD COLUMN abr FLOAT"))

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT width FROM download LIMIT 1"))
    except (OperationalError, ProgrammingError):
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE download ADD COLUMN width INTEGER"))
            await conn.execute(text("ALTER TABLE download ADD COLUMN height INTEGER"))
            await conn.execute(text("ALTER TABLE download ADD COLUMN vcodec TEXT"))
            await conn.execute(text("ALTER TABLE download ADD COLUMN acodec TEXT"))
            await conn.execute(text("ALTER TABLE download ADD COLUMN abr FLOAT"))

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT video_id FROM media_item LIMIT 1"))
    except (OperationalError, ProgrammingError):
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE media_item ADD COLUMN video_id TEXT"))
            await conn.execute(text("ALTER TABLE media_item ADD COLUMN playlist_pk INTEGER"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_media_item_video_id ON media_item (video_id)"))

    await _backfill_video_ids()


async def _backfill_video_ids() -> None:
    from sqlalchemy import select

    from services.playlists import video_id_of

    async with async_session() as session:
        rows = (
            await session.execute(select(MediaItem).where(MediaItem.video_id.is_(None)))
        ).scalars().all()
        for item in rows:
            item.video_id = video_id_of(item.source_url)
        if rows:
            await session.commit()
