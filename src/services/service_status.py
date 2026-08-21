"""Shared "is something site-wide currently degraded" heuristic. Used by both
`GET /api/settings/service-status` (the queue-page banner) and the periodic
`mediarvester.service.degraded` gauge (services/poller.py's `_refresh_gauges`,
feeding services/telemetry.py) -- kept in one place so the two never drift.
"""

import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import Download

# Substrings seen in yt-dlp errors that reflect a transient, site-side
# problem (rate limiting, a PO Token / bot-check hiccup, a format temporarily
# withheld) rather than something wrong with a specific video or with this
# app. Matched case-insensitively against recent failures' error text.
TRANSIENT_ERROR_PATTERNS = re.compile(
    r"403|forbidden|429|too many requests|"
    r"po token|n challenge|sign in to confirm|"
    r"requested format is not available|unable to download video data",
    re.IGNORECASE,
)
# How far back to look, and how many matching failures within that window
# count as "this isn't just one flaky video, something's degraded site-wide".
DEGRADED_WINDOW = timedelta(hours=2)
DEGRADED_THRESHOLD = 3


async def compute_service_status(session: AsyncSession) -> dict:
    """Not user-scoped: a YouTube-side block affects every owner the same
    way, and the DB is already the durable record of what failed.

    Deliberately doesn't predict *when* it'll clear -- no reliable way to
    know that (depends on an upstream fix or the site lifting an A/B test).
    "detected_since" is the one honest, useful data point: how long this
    has been going on.
    """
    cutoff = datetime.utcnow() - DEGRADED_WINDOW
    result = await session.execute(
        select(Download.created_at, Download.error)
        .where(Download.status == "error")
        .where(Download.created_at >= cutoff)
        .where(Download.error.isnot(None))
        .order_by(Download.created_at.asc())
    )
    matches = [row for row in result.all() if TRANSIENT_ERROR_PATTERNS.search(row.error)]

    if len(matches) < DEGRADED_THRESHOLD:
        return {"degraded": False, "detected_since": None, "recent_failures": len(matches)}
    return {
        "degraded": True,
        "detected_since": matches[0].created_at,
        "recent_failures": len(matches),
    }
