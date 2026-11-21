"""Classify yt-dlp error text into what it means for *what to do next*.

Shared by the retry strategy (services/retry_strategy.py: skip, change
clients, or just wait) and the canary (services/canary.py: what kind of
breakage a probe hit). The categories are about the action, not the wording:
YouTube's messages change, the decisions they drive don't.

Order matters -- the first matching rule wins, so the specific messages come
before the generic ones they contain ("Video unavailable. This video contains
content from X, who has blocked it from display on this website or
application" is an *embed* block, not a removed video).
"""

import re
from enum import Enum


class ErrorKind(str, Enum):
    # Refused by the embedded-player clients only; other clients may work.
    EMBED_BLOCKED = "embed_blocked"
    # 403 on the stream itself -- this client's URLs are being refused.
    FORBIDDEN = "forbidden"
    # Formats withheld or missing a URL (SABR, PO Token, "Requested format
    # is not available") -- this client set got nothing downloadable.
    FORMATS_MISSING = "formats_missing"
    # YouTube throttling this IP/session; changing clients won't help, time will.
    RATE_LIMITED = "rate_limited"
    # Needs a logged-in session (age gate, members-only, private-but-shared).
    AUTH_REQUIRED = "auth_required"
    # Not started yet (scheduled live, premiere) -- retry later.
    NOT_YET_AVAILABLE = "not_yet_available"
    # Gone or blocked for good (private, removed, terminated, geo-blocked).
    PERMANENT = "permanent"
    OTHER = "other"


_RULES: list[tuple[ErrorKind, re.Pattern]] = [
    (
        ErrorKind.EMBED_BLOCKED,
        re.compile(
            r"blocked it from display on this website or application"
            r"|playback on other websites has been disabled"
            r"|embedding (?:of this video )?(?:has been )?disabled",
            re.I,
        ),
    ),
    (
        ErrorKind.RATE_LIMITED,
        re.compile(
            r"\b429\b|too many requests|rate[- ]?limit|"
            r"sign in to confirm you(?:'|’)?re not a bot|try again later",
            re.I,
        ),
    ),
    (
        ErrorKind.AUTH_REQUIRED,
        re.compile(
            r"sign in to confirm your age|age[- ]restricted|members[- ]only|"
            r"join this channel|requires? (?:a )?(?:login|authentication)|use --cookies",
            re.I,
        ),
    ),
    (
        ErrorKind.NOT_YET_AVAILABLE,
        re.compile(r"live event will begin|premieres? in|this live event|is not live yet|upcoming", re.I),
    ),
    (
        ErrorKind.PERMANENT,
        re.compile(
            r"private video|video has been removed|account .*(?:terminated|closed)|"
            r"no longer available|not available in your country|"
            r"uploader has not made this video available|copyright claim|"
            r"video unavailable",
            re.I,
        ),
    ),
    (ErrorKind.FORBIDDEN, re.compile(r"\b403\b|forbidden", re.I)),
    (
        ErrorKind.FORMATS_MISSING,
        re.compile(
            r"requested format is not available|missing a url|sabr|po token|"
            r"only images are available|n challenge",
            re.I,
        ),
    ),
]

# Kinds an automatic retry can't fix: the video is gone, or it needs a login
# this owner doesn't have (a new cookies upload is a manual resubmit anyway).
NOT_RETRYABLE = frozenset({ErrorKind.PERMANENT, ErrorKind.AUTH_REQUIRED})


def classify(message: str | None) -> ErrorKind:
    if not message:
        return ErrorKind.OTHER
    for kind, pattern in _RULES:
        if pattern.search(message):
            return kind
    return ErrorKind.OTHER
