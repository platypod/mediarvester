"""No YouTube player_client may be pinned by default.

Every pin this project shipped (mweb, "tv_embedded", web_embedded) broke within
weeks while yt-dlp's own default client set kept working -- see
services/ytdlp_options.py. These guard every call site that builds options.
"""

from services import poller
from services.downloader import Downloader, _flat_extract_info
from services.ytdlp_options import youtube_opts


def _pinned(opts: dict) -> bool:
    return bool(opts.get("extractor_args", {}).get("youtube", {}).get("player_client"))


def test_base_options_pin_no_client():
    assert not _pinned(youtube_opts())
    assert youtube_opts()["js_runtimes"] == {"node": {}}


def test_explicit_override_is_passed_through():
    assert youtube_opts(["tv_samsung"])["extractor_args"] == {"youtube": {"player_client": ["tv_samsung"]}}


def test_download_options_pin_no_client():
    opts = Downloader()._build_opts(1, "someone", url="https://www.youtube.com/watch?v=abc")
    assert not _pinned(opts)
    assert opts["js_runtimes"] == {"node": {}}


def test_poller_options_pin_no_client():
    assert not _pinned(poller._YOUTUBE_OPTS)


def test_flat_lookup_pins_no_client(monkeypatch):
    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            seen.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {}

    monkeypatch.setattr("services.downloader.yt_dlp.YoutubeDL", FakeYDL)
    _flat_extract_info("https://www.youtube.com/watch?v=abc", "someone")
    assert seen and not _pinned(seen)
