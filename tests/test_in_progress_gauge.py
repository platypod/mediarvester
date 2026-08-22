"""services/downloader.py -- _observe_in_progress (the download.in_progress
OTel gauge feeding the "Now downloading" dashboard panel).

2026-08-22: the "(resolving title...)" placeholder shown in the UI for the
few seconds before a real title is known used to get emitted as a metric
label value too -- a throwaway Mimir series for a string nobody queries on,
and a meaningless bar in the state-timeline panel. Excluded from the gauge
now; the download simply doesn't appear there until a real title lands
(same as it never appearing at all before the download started).
"""

import pytest

from services.downloader import _RESOLVING_TITLE_PLACEHOLDER, _observe_in_progress
import services.downloader as downloader_module


@pytest.fixture(autouse=True)
def _reset_shared_downloader_state():
    # _observe_in_progress reads the module-level `downloader` singleton
    # directly (it's an OTel callback, registered once at import time) --
    # restore it so mutating it here can't leak into other test files.
    original = downloader_module.downloader._in_progress
    yield
    downloader_module.downloader._in_progress = original


def _set_in_progress(entries: dict):
    downloader_module.downloader._in_progress = entries


def test_resolving_title_placeholder_is_excluded(monkeypatch):
    _set_in_progress({1: {"owner": "reivi", "title": _RESOLVING_TITLE_PLACEHOLDER}})
    observations = list(_observe_in_progress(None))
    assert observations == []


def test_a_real_title_is_included():
    _set_in_progress({1: {"owner": "reivi", "title": "Some Real Video"}})
    observations = list(_observe_in_progress(None))
    assert len(observations) == 1
    assert observations[0].value == 1
    assert observations[0].attributes == {"owner": "reivi", "title": "Some Real Video"}


def test_a_mix_only_yields_the_resolved_ones():
    _set_in_progress(
        {
            1: {"owner": "reivi", "title": _RESOLVING_TITLE_PLACEHOLDER},
            2: {"owner": "reivi", "title": "Resolved Video"},
        }
    )
    observations = list(_observe_in_progress(None))
    assert len(observations) == 1
    assert observations[0].attributes["title"] == "Resolved Video"


def test_nothing_in_progress_yields_nothing():
    _set_in_progress({})
    assert list(_observe_in_progress(None)) == []
