"""services/downloader.py -- _verify_downloaded_file / _cleanup_stray_fragments.

Added 2026-08-22: existence-of-the-final-path was the only check before
this -- a truncated write (disk full mid-merge, a killed process) or a
corrupt container at the *expected* final path was never caught, and
stray per-format temp fragments (a stuck `.f299.mp4.part`, an orphaned
audio-only `.f251.webm`) could survive indefinitely once nothing ever
revisited that entry again. Uses real ffmpeg/ffprobe (both present in the
production image; assumed present here too, same as CI's runner).
"""

import subprocess

from services.downloader import _cleanup_stray_fragments, _verify_downloaded_file


def _make_real_video(path, duration_seconds: float = 2.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s=32x32:d={duration_seconds}",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True, capture_output=True,
    )


def test_a_real_valid_video_passes_verification(tmp_path):
    video = tmp_path / "video.mp4"
    _make_real_video(video, duration_seconds=2.0)
    assert _verify_downloaded_file(str(video), expected_duration=2.0) is None


def test_a_missing_file_fails_verification(tmp_path):
    problem = _verify_downloaded_file(str(tmp_path / "does-not-exist.mp4"), expected_duration=2.0)
    assert problem is not None


def test_a_zero_byte_file_fails_verification(tmp_path):
    video = tmp_path / "empty.mp4"
    video.write_bytes(b"")
    assert _verify_downloaded_file(str(video), expected_duration=2.0) is not None


def test_garbage_bytes_with_a_video_extension_fails_verification(tmp_path):
    video = tmp_path / "garbage.mp4"
    video.write_bytes(b"this is not a real video file, just garbage bytes" * 100)
    assert _verify_downloaded_file(str(video), expected_duration=2.0) is not None


def test_a_badly_truncated_video_fails_verification(tmp_path):
    # A real container, but chopped down to a fraction of its real data --
    # simulates a disk-full-mid-write / killed-process truncation.
    video = tmp_path / "truncated.mp4"
    _make_real_video(video, duration_seconds=5.0)
    full_bytes = video.read_bytes()
    video.write_bytes(full_bytes[: len(full_bytes) // 20])
    problem = _verify_downloaded_file(str(video), expected_duration=5.0)
    assert problem is not None


def test_verification_passes_without_an_expected_duration_to_compare_against(tmp_path):
    # yt-dlp doesn't always report a duration (e.g. a livestream) -- the
    # check should still confirm the file is readable, just skip the
    # length comparison.
    video = tmp_path / "video.mp4"
    _make_real_video(video, duration_seconds=2.0)
    assert _verify_downloaded_file(str(video), expected_duration=None) is None


def test_cleanup_removes_stray_fragments_sharing_the_stem(tmp_path):
    stem = "Vers la Tour Divine - Elden Ring - Épisode 14"
    part_file = tmp_path / f"{stem}.f299.mp4.part"
    orphan_audio = tmp_path / f"{stem}.f251.webm"
    part_file.write_bytes(b"partial data")
    orphan_audio.write_bytes(b"orphaned audio track")
    unrelated = tmp_path / f"{stem}.webp"
    unrelated.write_bytes(b"a real thumbnail, must survive")

    _cleanup_stray_fragments(str(tmp_path / f"{stem}.mp4"))

    assert not part_file.exists()
    assert not orphan_audio.exists()
    assert unrelated.exists()


def test_cleanup_does_not_touch_the_legitimate_final_file(tmp_path):
    stem = "Some Video"
    final = tmp_path / f"{stem}.mp4"
    final.write_bytes(b"the actual finished video")
    _cleanup_stray_fragments(str(final))
    assert final.exists()


def test_cleanup_is_a_noop_with_an_empty_path():
    _cleanup_stray_fragments("")  # must not raise


def test_cleanup_is_a_noop_when_the_directory_does_not_exist(tmp_path):
    _cleanup_stray_fragments(str(tmp_path / "nonexistent-dir" / "video.mp4"))  # must not raise
