"""scripts/bump_ytdlp.py -- the daily yt-dlp bump must rewrite only the pins."""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "bump_ytdlp", Path(__file__).resolve().parents[1] / "scripts" / "bump_ytdlp.py"
)
bump_ytdlp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump_ytdlp)

REQS = "fastapi>=0.111.0\n# yt-dlp==1999.1.1 in a comment stays\nyt-dlp==2026.10.26\nyt-dlp-ejs==0.8.0\n"


def test_bumps_both_pins_and_reports_changes():
    text, changes = bump_ytdlp.bump(REQS, {"yt-dlp": "2026.11.20", "yt-dlp-ejs": "0.8.1"})
    assert "yt-dlp==2026.11.20\n" in text
    assert "yt-dlp-ejs==0.8.1\n" in text
    assert "# yt-dlp==1999.1.1 in a comment stays" in text
    assert changes == ["yt-dlp 2026.10.26 -> 2026.11.20", "yt-dlp-ejs 0.8.0 -> 0.8.1"]


def test_no_change_when_already_latest():
    text, changes = bump_ytdlp.bump(REQS, {"yt-dlp": "2026.10.26", "yt-dlp-ejs": "0.8.0"})
    assert text == REQS and changes == []


def test_ejs_pin_is_not_mistaken_for_yt_dlp():
    text, _ = bump_ytdlp.bump("yt-dlp-ejs==0.8.0\nyt-dlp==1.0\n", {"yt-dlp": "2.0"})
    assert text == "yt-dlp-ejs==0.8.0\nyt-dlp==2.0\n"


def test_extras_are_preserved():
    text, _ = bump_ytdlp.bump("yt-dlp[default]==1.0\n", {"yt-dlp": "2.0"})
    assert text == "yt-dlp[default]==2.0\n"


def test_unpinned_package_is_an_error():
    with pytest.raises(ValueError):
        bump_ytdlp.bump("yt-dlp>=2026.7.4\n", {"yt-dlp": "2026.11.20"})


def test_repo_requirements_pin_every_bumped_package():
    reqs = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text()
    _, changes = bump_ytdlp.bump(reqs, {p: "0.0.0-probe" for p in bump_ytdlp.PACKAGES})
    assert len(changes) == len(bump_ytdlp.PACKAGES)
