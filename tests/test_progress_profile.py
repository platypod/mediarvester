"""services/downloader.py -- _profile_from_info and its merge into Download
via _update_progress. A bestvideo+bestaudio download fires the progress hook
once per format (one call with vcodec set, one with acodec set) -- the
Queue view needs the video fields from the first call to survive the
second, audio-only call, and vice versa.
"""

from services.downloader import Downloader, _profile_from_info


def test_profile_from_info_picks_up_video_fields():
    info = {"vcodec": "avc1.640028", "acodec": "none", "width": 1920, "height": 1080}
    assert _profile_from_info(info) == {
        "vcodec": "avc1.640028",
        "width": 1920,
        "height": 1080,
    }


def test_profile_from_info_picks_up_audio_fields():
    info = {"vcodec": "none", "acodec": "mp4a.40.2", "abr": 128.0}
    assert _profile_from_info(info) == {"acodec": "mp4a.40.2", "abr": 128.0}


def test_profile_from_info_ignores_none_and_missing_fields():
    assert _profile_from_info({}) == {}
    assert _profile_from_info({"vcodec": "none", "acodec": "none"}) == {}


async def test_update_progress_merges_video_then_audio_calls(session, make_download):
    dl = await make_download(session, url="https://www.youtube.com/watch?v=abc123")
    downloader = Downloader()

    await downloader._update_progress(
        dl.id, 10.0, None, None, "Some Video",
        profile={"vcodec": "avc1.640028", "width": 1920, "height": 1080},
    )
    await session.refresh(dl)
    assert dl.vcodec == "avc1.640028"
    assert dl.width == 1920
    assert dl.acodec is None

    await downloader._update_progress(
        dl.id, 20.0, None, None, "Some Video",
        profile={"acodec": "mp4a.40.2", "abr": 128.0},
    )
    await session.refresh(dl)
    # Video fields from the first call survive the audio-only second call.
    assert dl.vcodec == "avc1.640028"
    assert dl.width == 1920
    assert dl.height == 1080
    assert dl.acodec == "mp4a.40.2"
    assert dl.abr == 128.0


async def test_update_progress_with_no_profile_leaves_existing_fields_untouched(session, make_download):
    dl = await make_download(session, url="https://example.com/v1", vcodec="h264", width=1280)
    downloader = Downloader()

    await downloader._update_progress(dl.id, 50.0, None, None, None, profile=None)
    await session.refresh(dl)
    assert dl.vcodec == "h264"
    assert dl.width == 1280
