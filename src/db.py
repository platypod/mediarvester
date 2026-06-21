from datetime import datetime
from os import environ

from sqlalchemy import ForeignKey, text
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
    status: Mapped[str] = mapped_column(default="queued")  # queued|downloading|done|error
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None]
    owner: Mapped[str] = mapped_column(default="anonymous", server_default="anonymous")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    finished_at: Mapped[datetime | None]


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
