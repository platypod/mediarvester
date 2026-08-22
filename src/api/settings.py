import os
from datetime import datetime
from os import environ
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, is_admin
from db import get_session
from services.downloader import COOKIES_ROOT
from services.service_status import compute_service_status

router = APIRouter(prefix="/api/settings", tags=["settings"])

GITHUB_URL = "https://github.com/platypod/mediarvester"


class MeRead(BaseModel):
    user: str
    is_admin: bool


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
async def get_me(user: str = Depends(get_current_user), admin: bool = Depends(is_admin)):
    return {"user": user, "is_admin": admin}


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


class ServiceStatus(BaseModel):
    degraded: bool
    detected_since: datetime | None
    recent_failures: int


@router.get("/service-status", response_model=ServiceStatus)
async def get_service_status(session: AsyncSession = Depends(get_session)):
    return await compute_service_status(session)
