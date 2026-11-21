import os
from datetime import datetime
from os import environ
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, is_admin
from db import get_session
from services import canary, cookies_health, jellyfin
from services.downloader import COOKIES_ROOT
from services.service_status import compute_service_status

router = APIRouter(prefix="/api/settings", tags=["settings"])

GITHUB_URL = "https://github.com/platypod/mediarvester"


class MeRead(BaseModel):
    user: str
    is_admin: bool
    jellyfin_enabled: bool


class VersionInfo(BaseModel):
    version: str
    github_url: str


@router.get("/version", response_model=VersionInfo)
async def get_version():
    return {"version": environ.get("VERSION", "dev"), "github_url": GITHUB_URL}


class CookiesCheck(BaseModel):
    """The latest canary probe made with this owner's cookies."""

    ok: bool
    checked_at: datetime
    # Still a login after talking to YouTube? False = YouTube rotated it.
    logged_in_after: bool | None
    kind: str | None


class CookiesStatus(BaseModel):
    has_cookies: bool
    uploaded_at: datetime | None
    # Is the file a logged-in YouTube session (LOGIN_INFO + SAPISID)? None
    # without a file. Re-read on every request: yt-dlp rewrites the file
    # after each download, so this can change without any upload.
    logged_in: bool | None = None
    last_check: CookiesCheck | None = None


def _cookies_status(user: str) -> dict:
    path = Path(COOKIES_ROOT) / f"{user}.txt"
    if not path.exists():
        return {"has_cookies": False, "uploaded_at": None}
    status = {
        "has_cookies": True,
        "uploaded_at": datetime.fromtimestamp(path.stat().st_mtime),
        "logged_in": cookies_health.is_logged_in(path),
    }
    result = canary.latest(user)
    if result:
        failed = next((p for p in result.probes if not p.ok), None)
        status["last_check"] = {
            "ok": result.ok,
            "checked_at": result.checked_at,
            "logged_in_after": result.logged_in_after,
            "kind": failed.kind if failed else None,
        }
    return status


@router.get("/me", response_model=MeRead)
async def get_me(user: str = Depends(get_current_user), admin: bool = Depends(is_admin)):
    return {"user": user, "is_admin": admin, "jellyfin_enabled": jellyfin.enabled()}


@router.get("/cookies", response_model=CookiesStatus)
async def get_cookies_status(user: str = Depends(get_current_user)):
    return _cookies_status(user)


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
    return _cookies_status(user)


class CanaryStatus(BaseModel):
    # None until the first canary run after startup.
    ok: bool | None
    checked_at: datetime | None
    kind: str | None


class ServiceStatus(BaseModel):
    degraded: bool
    detected_since: datetime | None
    recent_failures: int
    canary: CanaryStatus


@router.get("/service-status", response_model=ServiceStatus)
async def get_service_status(session: AsyncSession = Depends(get_session)):
    return await compute_service_status(session)
