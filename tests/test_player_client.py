"""The pinned YouTube player_client must be a client yt-dlp actually knows.

yt-dlp skips an unknown name with only a warning and silently falls back to its
default clients, so a typo (v1.15.4 shipped "tv_embedded") goes unnoticed.
"""

from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS

from services.downloader import YOUTUBE_PLAYER_CLIENT


def test_pinned_player_client_is_known_to_yt_dlp():
    assert YOUTUBE_PLAYER_CLIENT in INNERTUBE_CLIENTS
