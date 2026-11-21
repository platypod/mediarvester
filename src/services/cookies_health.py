"""Does a cookies file still hold a logged-in YouTube session?

Mirrors yt-dlp's own test (extractor/youtube/_base.py `_has_auth_cookies`):
LOGIN_INFO plus one of the SAPISID cookies. YouTube clears LOGIN_INFO when it
rotates a session, and yt-dlp writes the cookie jar back into the same file
after every run -- so a file that was fine on upload can silently stop being
a login. That changes which client set yt-dlp picks and loses access to
age-gated/members-only videos, with nothing but a yt-dlp warning in the logs
(2026-11-21: reivi's re-uploaded file was not a login at all).
"""

import time
from pathlib import Path

_YOUTUBE_DOMAINS = (".youtube.com", "youtube.com", "www.youtube.com")
_SID_COOKIES = frozenset({"SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"})


def youtube_cookie_names(text: str, now: float | None = None) -> set[str]:
    """Names of unexpired YouTube cookies in a Netscape cookies file."""
    now = time.time() if now is None else now
    names = set()
    for line in text.splitlines():
        # "#HttpOnly_" is a real cookie line, not a comment.
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 7 or fields[0] not in _YOUTUBE_DOMAINS:
            continue
        try:
            expires = int(fields[4])
        except ValueError:
            continue
        # 0 = session cookie: valid as far as the file can tell.
        if expires and expires < now:
            continue
        names.add(fields[5])
    return names


def is_logged_in_text(text: str, now: float | None = None) -> bool:
    names = youtube_cookie_names(text, now)
    return "LOGIN_INFO" in names and bool(names & _SID_COOKIES)


def is_logged_in(path: str | Path) -> bool:
    try:
        return is_logged_in_text(Path(path).read_text(errors="replace"))
    except OSError:
        return False
