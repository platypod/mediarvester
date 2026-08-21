"""services/service_status.py -- compute_service_status.

The degraded-service banner added after the 2026-08-20/21 incident: 3+
recent failures matching a known transient-error pattern within a 2h window
means "something's wrong site-wide", not "one flaky video". Getting the
threshold/window/pattern-matching wrong either spams a false banner on
ordinary one-off failures, or stays silent through a real outage.
"""

from datetime import datetime, timedelta

from services.service_status import DEGRADED_THRESHOLD, DEGRADED_WINDOW, compute_service_status


async def _make_error(session, make_download, *, error: str, age: timedelta = timedelta(minutes=1)):
    return await make_download(
        session,
        status="error",
        error=error,
        created_at=datetime.utcnow() - age,
    )


async def test_no_failures_is_not_degraded(session):
    status = await compute_service_status(session)
    assert status == {"degraded": False, "detected_since": None, "recent_failures": 0}


async def test_below_threshold_is_not_degraded(session, make_download):
    for _ in range(DEGRADED_THRESHOLD - 1):
        await _make_error(session, make_download, error="HTTP Error 403: Forbidden")
    status = await compute_service_status(session)
    assert status["degraded"] is False
    assert status["recent_failures"] == DEGRADED_THRESHOLD - 1


async def test_at_threshold_is_degraded(session, make_download):
    for _ in range(DEGRADED_THRESHOLD):
        await _make_error(session, make_download, error="HTTP Error 403: Forbidden")
    status = await compute_service_status(session)
    assert status["degraded"] is True
    assert status["recent_failures"] == DEGRADED_THRESHOLD


async def test_detected_since_is_the_earliest_matching_failure(session, make_download):
    ages = [timedelta(hours=1), timedelta(minutes=30), timedelta(minutes=5)]
    expected_earliest = datetime.utcnow() - ages[0]
    for age in ages:
        await _make_error(session, make_download, error="429 Too Many Requests", age=age)
    status = await compute_service_status(session)
    assert status["degraded"] is True
    # Allow a small tolerance for the time elapsed during the test itself.
    assert abs((status["detected_since"] - expected_earliest).total_seconds()) < 5


async def test_failures_outside_the_window_do_not_count(session, make_download):
    old = DEGRADED_WINDOW + timedelta(minutes=1)
    for _ in range(DEGRADED_THRESHOLD + 2):
        await _make_error(session, make_download, error="HTTP Error 403: Forbidden", age=old)
    status = await compute_service_status(session)
    assert status == {"degraded": False, "detected_since": None, "recent_failures": 0}


async def test_non_transient_error_text_does_not_count(session, make_download):
    # A per-video problem (e.g. genuinely deleted/private) isn't evidence of
    # a site-wide issue -- only the known transient-pattern substrings count.
    for _ in range(DEGRADED_THRESHOLD + 2):
        await _make_error(session, make_download, error="Video unavailable: this video has been removed")
    status = await compute_service_status(session)
    assert status["degraded"] is False
    assert status["recent_failures"] == 0


async def test_non_error_status_rows_are_ignored(session, make_download):
    for _ in range(DEGRADED_THRESHOLD + 2):
        await make_download(session, status="done", error=None)
    status = await compute_service_status(session)
    assert status["recent_failures"] == 0


async def test_matching_is_case_insensitive():
    from services.service_status import TRANSIENT_ERROR_PATTERNS

    assert TRANSIENT_ERROR_PATTERNS.search("Sign in to confirm you're not a bot")
    assert TRANSIENT_ERROR_PATTERNS.search("PO Token invalid")
    assert TRANSIENT_ERROR_PATTERNS.search("Requested format is not available")
    assert not TRANSIENT_ERROR_PATTERNS.search("no files were successfully downloaded")
