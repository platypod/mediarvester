"""services/downloader.py -- the format selector must never silently settle
for YouTube's pre-muxed 360p stream (itag 18). See
docs/incidents/2026-10-04-silent-360p-fallback.md.
"""

import pytest

from services.downloader import Downloader, _format_for, _YtDlpLogAdapter


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc",
        "https://youtube.com/@creator/videos",
        "https://m.youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "https://www.youtube.com/playlist?list=PL123",
    ],
)
def test_youtube_has_no_muxed_fallback(url):
    assert _format_for(url) == "bv*+ba"


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/user/status/1",
        "https://www.instagram.com/reel/abc/",
        "https://notyoutube.com/watch?v=abc",
        "https://youtube.com.evil.example/watch?v=abc",
        "",
    ],
)
def test_other_platforms_keep_muxed_fallback(url):
    assert _format_for(url) == "bv*+ba/b"


def test_build_opts_applies_format_for_url():
    opts = Downloader()._build_opts(1, "anonymous", url="https://www.youtube.com/watch?v=abc")
    assert opts["format"] == "bv*+ba"


def test_log_adapter_remembers_po_token_warning():
    adapter = _YtDlpLogAdapter(1)
    adapter.warning("mweb client https formats require a GVS PO Token which was not provided.")
    adapter.warning("something else")
    assert "GVS PO Token" in adapter.po_token_warning
    assert adapter.last_error == "something else"
