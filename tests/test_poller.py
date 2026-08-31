"""services/poller.py -- discovery dedup, tab resolution, and upload-date parsing.

Real yt-dlp network calls are mocked out throughout -- these test the
surrounding logic (what counts as "already have it", which tabs get
scanned, how a degraded scan is surfaced) rather than yt-dlp itself.
"""

from datetime import datetime

import services.poller as poller_module
from services.poller import _parse_upload_date, _relevant_tab_urls, _poll_source


def test_parse_upload_date_valid():
    ts = _parse_upload_date("20260821")
    assert ts is not None
    dt = datetime.utcfromtimestamp(ts)
    assert (dt.year, dt.month, dt.day) == (2026, 8, 21)


def test_parse_upload_date_none_when_absent():
    assert _parse_upload_date(None) is None


def test_parse_upload_date_empty_string():
    assert _parse_upload_date("") is None


class _FakeSpan:
    def set_attribute(self, *a, **kw):
        pass


# --- discovery filter -------------------------------------------------------
# These call the filter the way yt-dlp itself does. Every other test in this
# file mocks `_new_entries_since` wholesale, which is exactly how a filter that
# never collected anything shipped and then ran for weeks: each poll reported
# "0 new entries, no error" while re-walking the whole back catalog. See
# `_build_match_filter`'s docstring.

_CUTOFF = 1_000_000.0
_AFTER = _CUTOFF + 10_000.0
_BEFORE = _CUTOFF - 10_000.0


def _yt_dlp_final_incomplete() -> set:
    """What yt-dlp passes as `incomplete` on its last call during a
    `download=False` scan: `YoutubeDL._format_fields`, a non-empty set.
    """
    from yt_dlp import YoutubeDL

    fields = YoutubeDL._format_fields
    assert isinstance(fields, set) and fields, (
        "yt-dlp's _format_fields is no longer a non-empty set -- the assumption "
        "in _build_match_filter about how `incomplete` is passed needs rechecking"
    )
    return fields


def test_match_filter_collects_on_yt_dlp_final_call_which_passes_a_truthy_set():
    # The regression: `incomplete` is truthy on EVERY call a download=False
    # scan makes, so `if incomplete: return None` collected nothing, ever.
    collected: dict[str, dict] = {}
    match_filter = poller_module._build_match_filter(_CUTOFF, collected)

    verdict = match_filter({"id": "new1", "timestamp": _AFTER}, incomplete=_yt_dlp_final_incomplete())

    assert verdict is None
    assert list(collected) == ["new1"]


def test_match_filter_rejects_older_entry_so_break_on_reject_can_stop_the_walk():
    # Returning a rejection string is what raises RejectedVideoReached and
    # stops the scan. Never returning one is why polls walked the full catalog.
    collected: dict[str, dict] = {}
    match_filter = poller_module._build_match_filter(_CUTOFF, collected)

    verdict = match_filter({"id": "old1", "timestamp": _BEFORE}, incomplete=_yt_dlp_final_incomplete())

    assert isinstance(verdict, str) and verdict
    assert collected == {}


def test_match_filter_keeps_one_entry_per_video_and_prefers_the_richer_copy():
    # The filter fires on the flat probe and again with full metadata.
    collected: dict[str, dict] = {}
    match_filter = poller_module._build_match_filter(_CUTOFF, collected)

    match_filter({"id": "v1", "timestamp": _AFTER}, incomplete=True)
    match_filter(
        {"id": "v1", "timestamp": _AFTER, "webpage_url": "https://www.youtube.com/watch?v=v1"},
        incomplete=_yt_dlp_final_incomplete(),
    )

    assert len(collected) == 1
    assert collected["v1"]["webpage_url"] == "https://www.youtube.com/watch?v=v1"


def test_match_filter_stays_undecided_on_a_dateless_preliminary_probe():
    # `incomplete is True` is the preliminary flat stage -- the full fetch may
    # still supply a date, so don't collect a stub for the whole catalog.
    collected: dict[str, dict] = {}
    match_filter = poller_module._build_match_filter(_CUTOFF, collected)

    assert match_filter({"id": "v1"}, incomplete=True) is None
    assert collected == {}


def test_match_filter_lets_a_dateless_entry_through_on_the_final_call():
    # Extractor exposes no date and there's no further fetch coming: let it
    # through and let the Download/MediaItem dedupe do the work.
    collected: dict[str, dict] = {}
    match_filter = poller_module._build_match_filter(_CUTOFF, collected)

    verdict = match_filter({"id": "v1"}, incomplete=_yt_dlp_final_incomplete())

    assert verdict is None
    assert list(collected) == ["v1"]


def test_match_filter_judges_on_upload_date_when_timestamp_is_absent():
    collected: dict[str, dict] = {}
    match_filter = poller_module._build_match_filter(
        _parse_upload_date("20260601"), collected
    )

    assert match_filter({"id": "new", "upload_date": "20260830"}, incomplete=_yt_dlp_final_incomplete()) is None
    assert match_filter({"id": "old", "upload_date": "20260501"}, incomplete=_yt_dlp_final_incomplete())
    assert list(collected) == ["new"]


# --- scan error visibility --------------------------------------------------
# `ignoreerrors=True` makes yt-dlp's trouble() record a retcode instead of
# raising, so the `except DownloadError` around extract_info is unreachable and
# an aborted scan used to report success. _ScanLogger is what makes it visible.


def _abort_message(failures: int = 3) -> str:
    return (
        f'ERROR: Skipping the remaining entries in playlist "Creator - Videos" '
        f"since {failures} items failed extraction"
    )


def test_scan_logger_reports_nothing_when_nothing_failed():
    assert poller_module._ScanLogger().failure(found_entries=True) is None


def test_single_entry_failure_is_tolerated_when_the_scan_still_found_entries():
    # A stray private/age-gated video must not flag the whole source as
    # degraded -- that is the point of ignoreerrors here.
    scan_log = poller_module._ScanLogger()
    scan_log.error("ERROR: [youtube] abc: Video unavailable")

    assert scan_log.failure(found_entries=True) is None


def test_a_real_abort_is_reported_even_when_some_entries_were_found():
    scan_log = poller_module._ScanLogger()
    scan_log.error("ERROR: [youtube] abc: Video unavailable")
    scan_log.error(_abort_message())

    failure = scan_log.failure(found_entries=True)
    assert failure is not None and "Skipping the remaining entries" in failure


def test_errors_with_nothing_found_are_reported_as_a_systemic_failure():
    # Stale cookies / every request rejected: no abort marker, but the scan
    # has nothing to show. This is the "silently reports zero new videos
    # forever" case the error signal exists for.
    scan_log = poller_module._ScanLogger()
    scan_log.error("ERROR: [youtube] Sign in to confirm you're not a bot")

    failure = scan_log.failure(found_entries=False)
    assert failure is not None and "not a bot" in failure


def test_scan_logger_caps_the_reported_errors_and_counts_the_rest():
    scan_log = poller_module._ScanLogger()
    for i in range(5):
        scan_log.error(f"ERROR: entry {i} failed")
    scan_log.error(_abort_message())

    failure = scan_log.failure(found_entries=True)
    assert failure.count(";") == 2  # first three, joined
    assert "(+3 more)" in failure


def test_scan_logger_does_not_treat_warnings_as_a_degraded_scan():
    # Setting a logger bypasses yt-dlp's no_warnings filter, so warnings now
    # arrive here -- they must not flip the source into a degraded state.
    scan_log = poller_module._ScanLogger()
    scan_log.warning("WARNING: Falling back to generic extractor")

    assert scan_log.failure(found_entries=False) is None


def test_abort_marker_still_matches_yt_dlps_own_wording():
    """If yt-dlp rewords its skip_playlist_after_errors message, every real
    abort would silently downgrade to "tolerated" -- fail loudly instead.
    """
    import inspect

    from yt_dlp import YoutubeDL

    assert poller_module._ScanLogger._ABORT_MARKER in inspect.getsource(YoutubeDL), (
        "yt-dlp changed its skip_playlist_after_errors wording -- "
        "_ScanLogger._ABORT_MARKER no longer detects a real abort"
    )


def test_yt_dlp_routes_report_error_to_the_scan_logger_under_ignoreerrors():
    """The whole point: with ignoreerrors=True yt-dlp does NOT raise, so this
    is the only path by which an aborted scan can be noticed.
    """
    from yt_dlp import YoutubeDL

    scan_log = poller_module._ScanLogger()
    with YoutubeDL({"ignoreerrors": True, "quiet": True, "logger": scan_log}) as ydl:
        # Does not raise under ignoreerrors -- trouble() just sets a retcode,
        # which is why the except DownloadError branch cannot see this.
        ydl.report_error('Skipping the remaining entries in playlist "x"')

    assert scan_log.aborted
    assert scan_log.failure(found_entries=True) is not None


def test_relevant_tab_urls_resolves_channel_tabs(monkeypatch):
    monkeypatch.setattr(
        poller_module,
        "_extract_flat",
        lambda url, cookies: {
            "entries": [
                {"_type": "playlist", "webpage_url": "https://www.youtube.com/@Creator/videos"},
                {"_type": "playlist", "webpage_url": "https://www.youtube.com/@Creator/shorts"},
                {"_type": "playlist", "webpage_url": "https://www.youtube.com/@Creator/streams"},
                {"_type": "playlist", "webpage_url": "https://www.youtube.com/@Creator/playlists"},
            ]
        },
    )
    urls = _relevant_tab_urls("https://www.youtube.com/@Creator", include_shorts=False, cookies=None)
    assert urls == [
        "https://www.youtube.com/@Creator/videos",
        "https://www.youtube.com/@Creator/streams",
    ]


def test_relevant_tab_urls_includes_shorts_when_asked(monkeypatch):
    monkeypatch.setattr(
        poller_module,
        "_extract_flat",
        lambda url, cookies: {
            "entries": [
                {"_type": "playlist", "webpage_url": "https://www.youtube.com/@Creator/videos"},
                {"_type": "playlist", "webpage_url": "https://www.youtube.com/@Creator/shorts"},
            ]
        },
    )
    urls = _relevant_tab_urls("https://www.youtube.com/@Creator", include_shorts=True, cookies=None)
    assert "https://www.youtube.com/@Creator/shorts" in urls


def test_relevant_tab_urls_falls_back_to_original_url_when_not_a_channel(monkeypatch):
    # A direct playlist URL flat-extracts to actual videos, not nested
    # "playlist"-type tabs -- should be returned as-is, not resolved further.
    monkeypatch.setattr(
        poller_module,
        "_extract_flat",
        lambda url, cookies: {"entries": [{"_type": "video", "id": "abc123"}]},
    )
    urls = _relevant_tab_urls("https://youtube.com/playlist?list=PL1", include_shorts=False, cookies=None)
    assert urls == ["https://youtube.com/playlist?list=PL1"]


async def test_poll_source_skips_entries_already_in_download_table(
    session, make_source, make_download, monkeypatch
):
    source = await make_source(session, url="https://www.youtube.com/@Creator")
    await make_download(
        session, url="https://www.youtube.com/watch?v=already", owner=source.owner, status="done"
    )

    monkeypatch.setattr(poller_module, "_label_source", _async_noop)
    monkeypatch.setattr(
        poller_module,
        "_new_entries_since",
        lambda *a, **kw: (
            [{"webpage_url": "https://www.youtube.com/watch?v=already", "id": "already"}],
            None,
        ),
    )
    enqueued = []
    monkeypatch.setattr(poller_module.downloader, "enqueue", lambda *a, **kw: enqueued.append(a))
    monkeypatch.setattr(poller_module, "matching_known_playlist", _async_none)

    await _poll_source(source.id, _FakeSpan())

    assert enqueued == []


async def test_poll_source_skips_entries_already_in_media_item_table(
    session, make_source, make_media_item, monkeypatch
):
    source = await make_source(session, url="https://www.youtube.com/@Creator")
    # A MediaItem can exist without a matching Download row in some paths
    # (e.g. historical data) -- the media_item check is a second, independent
    # guard against re-downloading something already in the library.
    from db import Download

    dl = Download(url="https://example.com/placeholder", owner=source.owner, status="done")
    session.add(dl)
    await session.commit()
    await session.refresh(dl)
    await make_media_item(
        session, dl.id, source_url="https://www.youtube.com/watch?v=already", owner=source.owner
    )

    monkeypatch.setattr(poller_module, "_label_source", _async_noop)
    monkeypatch.setattr(
        poller_module,
        "_new_entries_since",
        lambda *a, **kw: (
            [{"webpage_url": "https://www.youtube.com/watch?v=already", "id": "already"}],
            None,
        ),
    )
    enqueued = []
    monkeypatch.setattr(poller_module.downloader, "enqueue", lambda *a, **kw: enqueued.append(a))
    monkeypatch.setattr(poller_module, "matching_known_playlist", _async_none)

    await _poll_source(source.id, _FakeSpan())

    assert enqueued == []


async def test_poll_source_enqueues_genuinely_new_entries_with_folder_hint(
    session, make_source, monkeypatch
):
    source = await make_source(session, url="https://www.youtube.com/@Creator")

    monkeypatch.setattr(poller_module, "_label_source", _async_noop)
    monkeypatch.setattr(
        poller_module,
        "_new_entries_since",
        lambda *a, **kw: (
            [{"webpage_url": "https://www.youtube.com/watch?v=new1", "id": "new1", "uploader": "Creator"}],
            None,
        ),
    )
    enqueued = []
    monkeypatch.setattr(poller_module.downloader, "enqueue", lambda *a, **kw: enqueued.append((a, kw)))

    async def fake_matching(owner, entry):
        return "Known Playlist"

    monkeypatch.setattr(poller_module, "matching_known_playlist", fake_matching)

    await _poll_source(source.id, _FakeSpan())

    assert len(enqueued) == 1
    args, kwargs = enqueued[0]
    assert args[1] == "https://www.youtube.com/watch?v=new1"
    assert kwargs.get("folder_hint") == "Known Playlist"


async def test_poll_source_records_last_poll_error_on_exception(session, make_source, monkeypatch):
    source = await make_source(session, url="https://www.youtube.com/@Creator")
    monkeypatch.setattr(poller_module, "_label_source", _async_noop)

    def raise_error(*a, **kw):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(poller_module, "_new_entries_since", raise_error)

    await _poll_source(source.id, _FakeSpan())

    await session.refresh(source)
    assert source.last_poll_error == "network exploded"


async def test_poll_source_records_degraded_error_without_raising(session, make_source, monkeypatch):
    source = await make_source(session, url="https://www.youtube.com/@Creator")
    monkeypatch.setattr(poller_module, "_label_source", _async_noop)
    monkeypatch.setattr(
        poller_module, "_new_entries_since", lambda *a, **kw: ([], "some tabs failed to scan")
    )

    await _poll_source(source.id, _FakeSpan())

    await session.refresh(source)
    assert source.last_poll_error == "some tabs failed to scan"
    assert source.last_polled_at is not None


async def test_poll_source_disabled_source_is_a_noop(session, make_source, monkeypatch):
    source = await make_source(session, url="https://www.youtube.com/@Creator", enabled=False)
    called = []
    monkeypatch.setattr(poller_module, "_new_entries_since", lambda *a, **kw: called.append(1))
    await _poll_source(source.id, _FakeSpan())
    assert called == []


async def _async_noop(*a, **kw):
    return None


async def _async_none(*a, **kw):
    return None


async def test_concurrent_polls_never_scan_youtube_at_the_same_time(
    session, make_source, monkeypatch
):
    # 2026-08-22 fix: sources on the same poll_interval_minutes land in
    # lockstep forever (APScheduler's interval trigger counts from whenever
    # the job was added), and nothing stopped their scans running
    # concurrently -- confirmed happening for real: 3 sources polled within
    # the same second, YouTube rate-limited within 30s. _youtube_scan_lock
    # should mean only one source's scan is ever actually in flight at once,
    # no matter how many poll_source calls overlap.
    source_a = await make_source(session, url="https://www.youtube.com/@A")
    source_b = await make_source(session, url="https://www.youtube.com/@B")
    monkeypatch.setattr(poller_module, "_label_source", _async_noop)
    monkeypatch.setattr(poller_module.downloader, "enqueue", lambda *a, **kw: None)

    concurrent_scans = 0
    max_concurrent_scans = 0

    def fake_new_entries_since(*a, **kw):
        nonlocal concurrent_scans, max_concurrent_scans
        concurrent_scans += 1
        max_concurrent_scans = max(max_concurrent_scans, concurrent_scans)
        try:
            # Simulate a slow scan -- long enough that, without the lock,
            # the two polls below would clearly overlap.
            import time

            time.sleep(0.05)
            return [], None
        finally:
            concurrent_scans -= 1

    monkeypatch.setattr(poller_module, "_new_entries_since", fake_new_entries_since)

    import asyncio

    await asyncio.gather(
        _poll_source(source_a.id, _FakeSpan()),
        _poll_source(source_b.id, _FakeSpan()),
    )

    assert max_concurrent_scans == 1
