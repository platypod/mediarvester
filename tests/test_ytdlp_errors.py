"""services/ytdlp_errors.py -- real yt-dlp messages map to the right action."""

import pytest

from services.ytdlp_errors import NOT_RETRYABLE, ErrorKind, classify


@pytest.mark.parametrize(
    "message,kind",
    [
        # Verbatim from prod, 2026-11-20 (web_embedded on eSO64xGEAow).
        (
            "ERROR: [youtube] eSO64xGEAow: Video unavailable. This video contains content from TF1 "
            "(TV Content), who has blocked it from display on this website or application Watch on YouTube",
            ErrorKind.EMBED_BLOCKED,
        ),
        # Prod, 2026-11-17 (mweb).
        ("ERROR: unable to download video data: HTTP Error 403: Forbidden", ErrorKind.FORBIDDEN),
        # Prod, 2026-11-21 (web_embedded SABR).
        (
            "ERROR: [youtube] smspqpgDKQo: Requested format is not available. Use --list-formats for a list",
            ErrorKind.FORMATS_MISSING,
        ),
        (
            "no media extracted for u: Requested format is not available (adaptive formats skipped: "
            "mweb client https formats require a GVS PO Token which was not provided)",
            ErrorKind.FORMATS_MISSING,
        ),
        ("Sign in to confirm you're not a bot. Use --cookies-from-browser", ErrorKind.RATE_LIMITED),
        ("Sign in to confirm you’re not a bot", ErrorKind.RATE_LIMITED),
        ("This content isn't available, try again later.", ErrorKind.RATE_LIMITED),
        ("HTTP Error 429: Too Many Requests", ErrorKind.RATE_LIMITED),
        ("Sign in to confirm your age. This video may be inappropriate", ErrorKind.AUTH_REQUIRED),
        ("Join this channel to get access to members-only content", ErrorKind.AUTH_REQUIRED),
        ("This live event will begin in 3 hours.", ErrorKind.NOT_YET_AVAILABLE),
        ("Premieres in 2 days", ErrorKind.NOT_YET_AVAILABLE),
        ("Private video. Sign in if you've been granted access", ErrorKind.PERMANENT),
        ("Video unavailable. This video has been removed by the uploader", ErrorKind.PERMANENT),
        ("The uploader has not made this video available in your country", ErrorKind.PERMANENT),
        ("no files were successfully downloaded", ErrorKind.OTHER),
        (None, ErrorKind.OTHER),
        ("", ErrorKind.OTHER),
    ],
)
def test_classify(message, kind):
    assert classify(message) is kind


def test_only_permanent_and_auth_are_not_retryable():
    assert NOT_RETRYABLE == {ErrorKind.PERMANENT, ErrorKind.AUTH_REQUIRED}
