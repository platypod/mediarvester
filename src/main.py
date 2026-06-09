import asyncio
import os
from contextlib import asynccontextmanager
from logging import basicConfig, getLogger
from os import environ
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.downloads import router as downloads_router
from api.media import router as media_router
from api.settings import router as settings_router
from api.shortcuts import router as shortcuts_router
from api.sources import router as sources_router
from db import create_all
from services.downloader import COOKIES_ROOT, MEDIA_ROOT, downloader
from services.poller import init_scheduler, scheduler

basicConfig(
    level=environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)-24s %(levelname)-8s %(message)s",
)
logger = getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(MEDIA_ROOT, exist_ok=True)
    os.makedirs(COOKIES_ROOT, exist_ok=True)
    os.makedirs("data", exist_ok=True)
    await create_all()
    downloader.set_loop(asyncio.get_event_loop())
    await init_scheduler()
    logger.info("mediarvester started")
    yield
    scheduler.shutdown(wait=False)
    downloader._executor.shutdown(wait=False)
    logger.info("mediarvester stopped")


app = FastAPI(title="mediarvester", lifespan=lifespan)

app.include_router(downloads_router)
app.include_router(sources_router)
app.include_router(media_router)
app.include_router(settings_router)
app.include_router(shortcuts_router)

# Serve downloaded files so thumbnails are reachable from the UI
app.mount("/media-files", StaticFiles(directory=MEDIA_ROOT, check_dir=False), name="media-files")

# Serve the built Vue app in production.
# Static assets (js/css/icons) are served directly; everything else falls through
# to index.html so Vue Router handles client-side navigation (including /share).
dist = Path(__file__).parent.parent / "frontend" / "dist"
if dist.exists():
    app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        # Serve known static files from dist root (sw.js, manifest, icon, etc.)
        candidate = dist / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        # Everything else → SPA shell
        index = dist / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(status_code=404)
