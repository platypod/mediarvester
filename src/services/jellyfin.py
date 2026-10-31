"""Link a library item to its page on Jellyfin.

mediarvester files downloads into the two Jellyfin libraries (see
library_layout.py) but never talked to Jellyfin. This resolves a MediaItem to
the Jellyfin item that holds the same file, so the UI can deep-link to it.

Jellyfin 10.11's `/Items` has no filter on path, and its item ids are not
derivable from one, so the lookup is a title search narrowed by comparing each
hit's `Path` with where the file should be *as Jellyfin sees it*:
`JELLYFIN_MEDIA_ROOT` is the Jellyfin-side spelling of `MEDIA_ROOT` (prod's
Jellyfin is host-native and mounts the share elsewhere). Nothing is cached --
placement can move a file, which changes its Jellyfin id, and a lookup is one
cheap request made only when somebody clicks.

Disabled unless all of `JELLYFIN_URL`, `JELLYFIN_API_KEY`, `JELLYFIN_PUBLIC_URL`
and `JELLYFIN_MEDIA_ROOT` are set.
"""

import asyncio
import json
import logging
import re
import unicodedata
import urllib.parse
import urllib.request
from os import environ

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10
SEARCH_LIMIT = 50


def _config() -> tuple[str, str, str, str] | None:
    url = environ.get("JELLYFIN_URL", "").rstrip("/")
    key = environ.get("JELLYFIN_API_KEY", "")
    public = environ.get("JELLYFIN_PUBLIC_URL", "").rstrip("/")
    root = environ.get("JELLYFIN_MEDIA_ROOT", "").rstrip("/")
    if not (url and key and public and root):
        return None
    return url, key, public, root


def enabled() -> bool:
    return _config() is not None


def _norm(path: str) -> str:
    # macOS-hosted Jellyfin reports NFD paths; the DB holds whatever yt-dlp wrote.
    return unicodedata.normalize("NFC", path)


def _request(method: str, url: str, key: str, body: dict | None = None) -> dict | None:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f'MediaBrowser Token="{key}"')
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


_EPISODE_PREFIX = re.compile(r"^.* - S\d+E\d+ - ")


def _search_terms(title: str, local_path: str) -> list[str]:
    """What Jellyfin may have named the item, most likely first.

    The stored title is the video's own, but an episode's NFO carries the title
    with its "#10" marker trimmed (library_layout.episode_title), and that is
    the name Jellyfin indexes -- so the file name, which is built from it, is
    the second candidate.
    """
    stem = _EPISODE_PREFIX.sub("", local_path.rsplit("/", 1)[-1].rsplit(".", 1)[0])
    return [t for t in dict.fromkeys((title, stem)) if t]


def _find_item_id(title: str, local_path: str) -> str | None:
    cfg = _config()
    if not cfg:
        return None
    base, key, _, root = cfg
    expected = _norm(f"{root}/{local_path}")
    for term in _search_terms(title, local_path):
        query = urllib.parse.urlencode(
            {
                "Recursive": "true",
                "IncludeItemTypes": "Movie,Episode",
                "Fields": "Path",
                "SearchTerm": term,
                "Limit": SEARCH_LIMIT,
            }
        )
        result = _request("GET", f"{base}/Items?{query}", key) or {}
        for item in result.get("Items") or []:
            if _norm(item.get("Path") or "") == expected:
                return item.get("Id")
    return None


def _request_scan(local_path: str) -> None:
    cfg = _config()
    if not cfg:
        return
    base, key, _, root = cfg
    _request("POST", f"{base}/Library/Media/Updated", key, {"Updates": [{"Path": f"{root}/{local_path}", "UpdateType": "Created"}]})


async def item_url(title: str, local_path: str) -> str | None:
    """The Jellyfin page for this file, or None when Jellyfin doesn't have it
    (yet): the file is then reported to Jellyfin so a later click can succeed."""
    cfg = _config()
    if not cfg:
        return None
    public = cfg[2]
    loop = asyncio.get_running_loop()
    try:
        item_id = await loop.run_in_executor(None, _find_item_id, title, local_path)
        if item_id is None:
            await loop.run_in_executor(None, _request_scan, local_path)
            return None
    except Exception as exc:
        logger.warning("jellyfin lookup for %r failed: %s", local_path, exc)
        return None
    return f"{public}/web/#/details?id={item_id}"
