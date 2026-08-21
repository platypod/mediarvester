import os
import re
from datetime import datetime, timedelta
from os import environ
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from db import Download, get_session
from services.downloader import COOKIES_ROOT

router = APIRouter(prefix="/api/settings", tags=["settings"])

GITHUB_URL = "https://github.com/platypod/mediarvester"


class MeRead(BaseModel):
    user: str


class VersionInfo(BaseModel):
    version: str
    github_url: str


@router.get("/version", response_model=VersionInfo)
async def get_version():
    return {"version": environ.get("VERSION", "dev"), "github_url": GITHUB_URL}


class CookiesStatus(BaseModel):
    has_cookies: bool
    uploaded_at: datetime | None


@router.get("/me", response_model=MeRead)
async def get_me(user: str = Depends(get_current_user)):
    return {"user": user}


@router.get("/cookies", response_model=CookiesStatus)
async def get_cookies_status(user: str = Depends(get_current_user)):
    path = Path(COOKIES_ROOT) / f"{user}.txt"
    if path.exists():
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return {"has_cookies": True, "uploaded_at": mtime}
    return {"has_cookies": False, "uploaded_at": None}


@router.post("/cookies", response_model=CookiesStatus)
async def upload_cookies(
    file: UploadFile,
    user: str = Depends(get_current_user),
):
    if not file.filename or not file.filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="Please upload a .txt cookies file.")

    os.makedirs(COOKIES_ROOT, exist_ok=True)
    path = Path(COOKIES_ROOT) / f"{user}.txt"
    content = await file.read()

    # Basic sanity check — Netscape cookie files start with a comment
    if not content.strip().startswith(b"#"):
        raise HTTPException(
            status_code=400,
            detail="File doesn't look like a Netscape cookies file.",
        )

    path.write_bytes(content)
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return {"has_cookies": True, "uploaded_at": mtime}


# Substrings seen in yt-dlp errors that reflect a transient, site-side
# problem (rate limiting, a PO Token / bot-check hiccup, a format temporarily
# withheld) rather than something wrong with a specific video or with this
# app. Matched case-insensitively against recent failures' error text.
_TRANSIENT_ERROR_PATTERNS = re.compile(
    r"403|forbidden|429|too many requests|"
    r"po token|n challenge|sign in to confirm|"
    r"requested format is not available|unable to download video data",
    re.IGNORECASE,
)
# How far back to look, and how many matching failures within that window
# count as "this isn't just one flaky video, something's degraded site-wide".
_DEGRADED_WINDOW = timedelta(hours=2)
_DEGRADED_THRESHOLD = 3


class ServiceStatus(BaseModel):
    degraded: bool
    detected_since: datetime | None
    recent_failures: int


@router.get("/service-status", response_model=ServiceStatus)
async def get_service_status(session: AsyncSession = Depends(get_session)):
    """Surface a site-wide "downloads are currently failing" signal, derived
    from recent Download rows rather than tracked separately -- this is
    intentionally not user-scoped: a YouTube-side block affects every owner
    the same way, and the DB is already the durable record of what failed.

    We deliberately don't predict *when* it'll clear -- we have no reliable
    way to know that (it depends on an upstream fix or YouTube lifting an
    A/B test), so promising a specific recovery time would just be made up.
    "Detected since" is the one honest, useful data point: how long this has
    been going on.
    """
    cutoff = datetime.utcnow() - _DEGRADED_WINDOW
    result = await session.execute(
        select(Download.created_at, Download.error)
        .where(Download.status == "error")
        .where(Download.created_at >= cutoff)
        .where(Download.error.isnot(None))
        .order_by(Download.created_at.asc())
    )
    matches = [row for row in result.all() if _TRANSIENT_ERROR_PATTERNS.search(row.error)]

    if len(matches) < _DEGRADED_THRESHOLD:
        return {"degraded": False, "detected_since": None, "recent_failures": len(matches)}
    return {
        "degraded": True,
        "detected_since": matches[0].created_at,
        "recent_failures": len(matches),
    }
