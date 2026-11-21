"""What the next auto-retry should do, given why the last attempt failed.

Before this, every retry repeated the exact same options, so a failure that
was deterministic for those options (an embed block under web_embedded,
eSO64xGEAow, 2026-11-20) burned all four attempts identically, and a video
that was simply gone was retried for a day.

Now the error is classified (services/ytdlp_errors.py) and:

- gone / needs a login -> no auto-retry at all (a resubmit stays possible);
- refused by the embedded player -> yt-dlp's defaults minus web_embedded;
- 403 or no downloadable formats -> walk a short ladder of *other* client
  sets, then back to the defaults on the last attempt (a day later, by
  which time the daily yt-dlp bump has often shipped the real fix);
- rate limiting, not-yet-live, anything else -> same options, just wait.

The first attempt always uses yt-dlp's own defaults (services/ytdlp_options.py);
an override here only ever applies to a retry of something that already
failed. Ladder entries were picked from a canary-probe run of the five
videos involved in the 2026-11 incidents (tv_samsung and visionos passed all
five, alone or together; mweb failed 2/5; web_safari 5/5), and every name is
checked against yt-dlp's own client list by tests/test_retry_strategy.py.
"""

from dataclasses import dataclass

from services.ytdlp_errors import NOT_RETRYABLE, ErrorKind, classify

# yt-dlp syntax: "default" = its own default set, "-x" removes client x.
WITHOUT_EMBEDDED = ["default", "-web_embedded"]

# Indexed by the retry attempt number (1-based); past the end -> defaults.
STREAM_FAILURE_LADDER: list[list[str] | None] = [
    ["tv_samsung", "visionos"],
    ["tv_samsung"],
    ["visionos"],
    None,
]


@dataclass(frozen=True)
class RetryPlan:
    retry: bool
    kind: ErrorKind
    # None = yt-dlp's defaults (no override).
    player_client: list[str] | None = None


def plan(error: str | None, attempt: int) -> RetryPlan:
    """`attempt` is the retry about to be scheduled: 1 for the first retry."""
    kind = classify(error)
    if kind in NOT_RETRYABLE:
        return RetryPlan(retry=False, kind=kind)
    if kind is ErrorKind.EMBED_BLOCKED:
        return RetryPlan(retry=True, kind=kind, player_client=list(WITHOUT_EMBEDDED))
    if kind in (ErrorKind.FORBIDDEN, ErrorKind.FORMATS_MISSING):
        index = attempt - 1
        clients = STREAM_FAILURE_LADDER[index] if 0 <= index < len(STREAM_FAILURE_LADDER) else None
        return RetryPlan(retry=True, kind=kind, player_client=list(clients) if clients else None)
    return RetryPlan(retry=True, kind=kind)


def encode(player_client: list[str] | None) -> str | None:
    """Download.player_client column format: comma-separated, None = defaults."""
    return ",".join(player_client) if player_client else None


def decode(value: str | None) -> list[str] | None:
    return [c for c in value.split(",") if c] if value else None
