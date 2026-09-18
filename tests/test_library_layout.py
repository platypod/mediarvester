"""services/library_layout.py -- where a download lands and what describes it.

This decides the on-disk shape two Jellyfin libraries are read from, so a
misclassification is not cosmetic: a loose video placed under series/ becomes a
bogus one-episode show, and an episode placed under singles/ drops out of its
show's running order entirely.
"""

import json
import os

import pytest

from services import library_layout as L


def entry(**kw):
    base = {"title": "A Video", "uploader": "Creator", "id": "vid123"}
    base.update(kw)
    return base


# --- classification ------------------------------------------------------

def test_numbered_playlist_entry_is_a_series():
    e = entry(title="Elden Ring Run Lore #4", playlist_title="Elden Ring Run Lore")
    assert L.classify(e) == "series"


def test_loose_video_with_no_playlist_is_a_single():
    assert L.classify(entry()) == "singles"


def test_playlist_entry_with_no_resolvable_number_is_a_single():
    # Cannot be expressed as SxxExx, and Jellyfin will not order a show whose
    # episodes have no numbers -- better a movie than an unorderable episode.
    e = entry(title="Some Unnumbered Video", playlist_title="Cool Tech")
    assert L.classify(e) == "singles"


def test_playlist_index_is_enough_to_make_it_a_series():
    e = entry(title="Some Unnumbered Video", playlist_title="Cool Tech", playlist_index=3)
    assert L.classify(e) == "series"


@pytest.mark.parametrize("dump", ["Videos", "Vidéos", "les vidéos", "Feldup - Videos"])
def test_a_channel_uploads_tab_is_not_a_show(dump):
    # 138 unrelated uploads are not a series; they belong in singles/.
    e = entry(title="Findings N°31", playlist_title=dump, playlist_index=5)
    assert L.classify(e) == "singles"
    assert L.show_name(e) is None


def test_the_literal_NA_playlist_is_not_a_playlist():
    # yt-dlp renders an absent field as "NA" in some template forms; several
    # real folders were created that way.
    e = entry(playlist_title="NA", playlist_index=2)
    assert L.classify(e) == "singles"


# --- naming --------------------------------------------------------------

def test_show_name_includes_the_creator_to_avoid_collisions():
    # Two followed creators each have an "Outer Wilds" series; a tvshows
    # library keys a show on its folder name, so bare playlist names merge
    # the two into one show with interleaved episodes.
    a = entry(uploader="mistermv", playlist_title="Outer Wilds", playlist_index=1)
    b = entry(uploader="Shisheyu Mayamoto", playlist_title="Outer Wilds", playlist_index=1)
    assert L.show_name(a) != L.show_name(b)
    assert L.show_name(a) == "mistermv - Outer Wilds"


def test_series_target_is_the_jellyfin_episode_layout(tmp_path):
    e = entry(title="Guilliman ! - LORE 40K", playlist_title="LE TUNNEL",
              uploader="AlphaReplay", playlist_index=10)
    directory, stem = L.target_for(e, str(tmp_path), ".webm")
    assert directory == os.path.join(str(tmp_path), "series", "AlphaReplay - LE TUNNEL", "Season 01")
    assert stem == "AlphaReplay - LE TUNNEL - S01E10 - Guilliman ! - LORE 40K"


def test_singles_target_groups_by_creator(tmp_path):
    e = entry(title="I ordered EVERY Corset", uploader="Naomi Jon")
    directory, stem = L.target_for(e, str(tmp_path), ".webm")
    assert directory == os.path.join(str(tmp_path), "singles", "Naomi Jon")
    assert stem == "I ordered EVERY Corset"


def test_a_separator_in_a_title_never_becomes_a_directory(tmp_path):
    e = entry(title="AC/DC live")
    _, stem = L.target_for(e, str(tmp_path), ".webm")
    assert "/" not in stem


# --- episode number collisions -------------------------------------------

def _seed_episode(tmp_path, show, number, video_id, title="Something"):
    d = tmp_path / "series" / show / "Season 01"
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{show} - S01E{number:02d} - {title}"
    (d / f"{stem}.webm").write_text("x")
    (d / f"{stem}.info.json").write_text(json.dumps({"id": video_id}))
    return d


def test_two_different_videos_claiming_one_episode_number_do_not_collide(tmp_path):
    # Real case: a series had two episodes both marked "#12", five days apart.
    # Sharing one SxxExx path makes Jellyfin show one and silently drop the
    # other, so the second must move to the next free slot.
    show = "MrDeriv - KCD2"
    _seed_episode(tmp_path, show, 12, "firstvideo")
    e = entry(title="La vengeance et la fuite #12", playlist_title="KCD2",
              uploader="MrDeriv", id="secondvideo")
    _, stem = L.target_for(e, str(tmp_path), ".webm")
    assert "S01E13" in stem, stem


def test_the_same_video_keeps_its_own_episode_number(tmp_path):
    # A re-download must land back on its own path, not be pushed to a new
    # episode number every time.
    show = "MrDeriv - KCD2"
    _seed_episode(tmp_path, show, 12, "samevideo")
    e = entry(title="C'est une embuscade #12", playlist_title="KCD2",
              uploader="MrDeriv", id="samevideo")
    _, stem = L.target_for(e, str(tmp_path), ".webm")
    assert "S01E12" in stem, stem


def test_taken_stems_are_respected_before_anything_exists_on_disk(tmp_path):
    # A batch (migration, playlist download) plans many files at once, before
    # any of them exist -- without this they would all plan onto one path.
    e = entry(title="Ep #3", playlist_title="Show", uploader="C")
    taken = {"C - Show - S01E03 - Ep"}
    _, stem = L.target_for(e, str(tmp_path), ".webm", taken=taken)
    assert "S01E04" in stem, stem


# --- NFO -----------------------------------------------------------------

def test_episode_nfo_carries_what_jellyfin_orders_episodes_by():
    e = entry(title="Les Skavens #14", playlist_title="LE TUNNEL", uploader="AlphaReplay",
              description="Un long résumé", upload_date="20260716", duration=7200, id="abc")
    xml = L.episode_nfo(e, 14)
    assert "<season>1</season>" in xml
    assert "<episode>14</episode>" in xml
    assert "<aired>2026-07-16</aired>" in xml
    assert "Un long résumé" in xml
    assert 'uniqueid type="youtube"' in xml


def test_movie_nfo_carries_title_plot_and_date():
    e = entry(title="A Video", description="desc", upload_date="20260101", uploader="Naomi Jon")
    xml = L.movie_nfo(e)
    assert "<title>A Video</title>" in xml
    assert "<premiered>2026-01-01</premiered>" in xml
    assert "<studio>Naomi Jon</studio>" in xml


def test_nfo_escapes_markup_in_a_title():
    # Creator titles contain & and < regularly ("ORQUES & GOBELINS"); unescaped
    # they make the NFO invalid XML and Jellyfin ignores the whole file.
    xml = L.movie_nfo(entry(title="ORQUES & GOBELINS <the best>"))
    assert "&amp;" in xml and "&lt;" in xml


def test_a_missing_upload_date_does_not_emit_a_broken_date():
    xml = L.movie_nfo(entry(upload_date=None))
    assert "<premiered>" not in xml


# --- placement -----------------------------------------------------------

def test_place_moves_the_file_its_sidecars_and_writes_metadata(tmp_path):
    src_dir = tmp_path / "Creator"
    src_dir.mkdir()
    stem = "Les Skavens ! - LORE - Ep 14"
    video = src_dir / f"{stem}.webm"
    video.write_text("video")
    (src_dir / f"{stem}.info.json").write_text("{}")
    (src_dir / f"{stem}.webp").write_text("image")

    e = entry(title=stem, playlist_title="LE TUNNEL", uploader="AlphaReplay", id="abc")
    new = L.place(str(video), e, str(tmp_path))

    season = tmp_path / "series" / "AlphaReplay - LE TUNNEL" / "Season 01"
    assert new.startswith(str(season))
    names = set(os.listdir(season))
    base = "AlphaReplay - LE TUNNEL - S01E14 - Les Skavens ! - LORE"
    assert f"{base}.webm" in names
    assert f"{base}.info.json" in names
    # Jellyfin finds artwork by suffix, not by bare stem.
    assert f"{base}-thumb.webp" in names
    assert f"{base}.nfo" in names
    # ...and the show itself is described once, beside the season folder.
    assert (season.parent / "tvshow.nfo").exists()
    assert not video.exists()


def test_place_puts_an_unnumbered_video_in_singles_with_a_poster(tmp_path):
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    video = src_dir / "Just A Video.webm"
    video.write_text("video")
    (src_dir / "Just A Video.webp").write_text("image")

    new = L.place(str(video), entry(title="Just A Video", uploader="Naomi Jon"), str(tmp_path))
    out = tmp_path / "singles" / "Naomi Jon"
    assert new == str(out / "Just A Video.webm")
    assert (out / "Just A Video-poster.webp").exists()
    assert (out / "Just A Video.nfo").exists()


def test_place_leaves_the_file_alone_when_the_target_is_occupied(tmp_path):
    # Better a file in the wrong folder than one silently overwritten.
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    video = src_dir / "Dup.webm"
    video.write_text("new")
    out = tmp_path / "singles" / "Creator"
    out.mkdir(parents=True)
    (out / "Dup.webm").write_text("existing")

    new = L.place(str(video), entry(title="Dup"), str(tmp_path))
    assert new == str(video)
    assert (out / "Dup.webm").read_text() == "existing"


def test_place_never_raises_when_the_file_is_gone(tmp_path):
    missing = str(tmp_path / "nope.webm")
    assert L.place(missing, entry(), str(tmp_path)) == missing


def test_an_unnumbered_episode_joins_a_show_that_already_exists(tmp_path):
    # Most of these creators number nothing, so their shows are ordered by
    # upload date. Once such a show exists, its next episode must join it --
    # otherwise a show migrated as a show silently stops growing and its new
    # episodes land in the movies library instead.
    _seed_episode(tmp_path, "EGO - Une autre époque", 4, "older")
    e = entry(title="A New One", playlist_title="Une autre époque", uploader="EGO", id="new")
    assert L.classify(e, str(tmp_path)) == "series"
    _, stem = L.target_for(e, str(tmp_path), ".webm")
    assert "S01E05" in stem, stem


def test_an_unnumbered_video_with_no_existing_show_is_still_a_single(tmp_path):
    e = entry(title="A New One", playlist_title="Brand New Playlist", uploader="EGO")
    assert L.classify(e, str(tmp_path)) == "singles"


def test_a_multi_season_show_keeps_growing_in_its_latest_season(tmp_path):
    # One real library folder was laid out by hand as Season 01 (2020) /
    # 02 (2023) / 03 (2024). Appending today's episode to Season 01 would file
    # it under the oldest season of a show that is three seasons in.
    show = "Shisheyu Mayamoto - Stardew Valley"
    for season in (1, 2, 3):
        d = tmp_path / "series" / show / f"Season {season:02d}"
        d.mkdir(parents=True)
        (d / f"{show} - S{season:02d}E01 - Old.mkv").write_text("x")
    e = entry(title="A New One", playlist_title="Stardew Valley",
              uploader="Shisheyu Mayamoto", id="new")
    assert L.season_for(e, str(tmp_path)) == 3
    directory, stem = L.target_for(e, str(tmp_path), ".mkv")
    assert directory.endswith("Season 03")
    assert "S03E02" in stem, stem


def test_a_brand_new_show_starts_at_season_one(tmp_path):
    e = entry(title="Ep #1", playlist_title="Brand New", uploader="C")
    assert L.season_for(e, str(tmp_path)) == L.DEFAULT_SEASON
    _, stem = L.target_for(e, str(tmp_path), ".webm")
    assert "S01E01" in stem, stem


def test_episode_nfo_reports_the_season_it_was_filed_under():
    xml = L.episode_nfo(entry(), 4, season=3)
    assert "<season>3</season>" in xml and "<episode>4</episode>" in xml


def test_nfo_is_written_when_the_episode_title_starts_with_s(tmp_path):
    # Parsing the stem by splitting on " - S" grabs the TITLE's leading S
    # instead of the episode marker, the parse raises, and the episode is
    # silently left with no metadata. 17 real files were affected.
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    video = src_dir / "Satanés Égouts - Elden Ring #39.mkv"
    video.write_text("v")
    e = entry(title="Satanés Égouts - Elden Ring #39", playlist_title="Elden Ring",
              uploader="MrDeriv", id="abc")
    new = L.place(str(video), e, str(tmp_path))
    assert os.path.exists(os.path.splitext(new)[0] + ".nfo"), os.listdir(os.path.dirname(new))


# --- artwork ------------------------------------------------------------
#
# A tvshows library looks for the poster at the SHOW root, not on the
# episodes, so a series whose episodes all have artwork still renders as a
# blank tile without this. Of 32 real shows, 0 had a poster.

def _place_episode(tmp_path, title="Ep #4", with_image=True, **kw):
    src = tmp_path / "in"
    src.mkdir(exist_ok=True)
    v = src / f"{title}.webm"
    v.write_text("v")
    if with_image:
        (src / f"{title}.webp").write_bytes(b"x" * 2000)
    e = entry(title=title, playlist_title="A Show", uploader="C", id="vid1", **kw)
    return L.place(str(v), e, str(tmp_path)), e


def test_a_show_gets_a_poster_from_the_episode_that_creates_it(tmp_path):
    new, _ = _place_episode(tmp_path)
    show_dir = tmp_path / "series" / "C - A Show"
    assert (show_dir / "poster.webp").exists(), sorted(os.listdir(show_dir))
    # ...and the episode keeps its own thumb
    assert any(p.name.endswith("-thumb.webp") for p in (show_dir / "Season 01").iterdir())


def test_an_existing_show_poster_is_never_overwritten(tmp_path):
    _place_episode(tmp_path)
    poster = tmp_path / "series" / "C - A Show" / "poster.webp"
    poster.write_bytes(b"chosen by hand")
    src = tmp_path / "in2"
    src.mkdir()
    v = src / "Ep #5.webm"
    v.write_text("v")
    (src / "Ep #5.webp").write_bytes(b"y" * 2000)
    L.place(str(v), entry(title="Ep #5", playlist_title="A Show", uploader="C", id="v2"), str(tmp_path))
    assert poster.read_bytes() == b"chosen by hand"


def test_a_missing_thumbnail_is_fetched_from_youtube(monkeypatch, tmp_path):
    # yt-dlp silently writes no thumbnail for some items; the info dict still
    # carries the URL, so the item must not be left blank.
    calls = []

    class _Resp:
        def read(self): return b"i" * 5000
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        calls.append(getattr(req, "full_url", req))
        return _Resp()

    monkeypatch.setattr(L.urllib.request, "urlopen", fake_urlopen)
    new, _ = _place_episode(tmp_path, title="Ep #9", with_image=False,
                            thumbnail="https://i.ytimg.com/vi/vid1/maxresdefault.jpg")
    season = os.path.dirname(new)
    assert any(n.endswith("-thumb.jpg") for n in os.listdir(season)), os.listdir(season)
    assert calls, "no thumbnail was fetched"


def test_a_failed_thumbnail_fetch_never_breaks_the_download(monkeypatch, tmp_path):
    def boom(req, timeout=0):
        raise OSError("network down")

    monkeypatch.setattr(L.urllib.request, "urlopen", boom)
    new, _ = _place_episode(tmp_path, title="Ep #7", with_image=False,
                            thumbnail="https://example.invalid/x.jpg")
    assert os.path.exists(new)          # the media still landed


def test_a_tiny_response_is_not_accepted_as_artwork(monkeypatch, tmp_path):
    # An error page returns 200 with a short body; writing it would leave a
    # corrupt "image" that looks present to Jellyfin.
    class _Resp:
        def read(self): return b"404"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(L.urllib.request, "urlopen", lambda req, timeout=0: _Resp())
    new, _ = _place_episode(tmp_path, title="Ep #8", with_image=False,
                            thumbnail="https://example.invalid/x.jpg")
    season = os.path.dirname(new)
    assert not any(n.endswith("-thumb.jpg") for n in os.listdir(season))


def test_a_single_gets_a_poster_suffix_not_a_thumb(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    v = src / "Loose Video.webm"
    v.write_text("v")
    (src / "Loose Video.webp").write_bytes(b"x" * 2000)
    new = L.place(str(v), entry(title="Loose Video", uploader="Naomi Jon"), str(tmp_path))
    assert os.path.exists(os.path.join(os.path.dirname(new), "Loose Video-poster.webp"))


def test_a_frame_is_extracted_when_youtube_has_no_thumbnail(monkeypatch, tmp_path):
    # Older/hand-curated items cannot be identified on YouTube, so there is no
    # thumbnail to fetch. A frame from the file itself is better than a blank
    # tile, and storing it beside the media survives a library rebuild.
    def no_thumb(entry, dest):
        return False

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        # ffmpeg writes its output file
        open(cmd[-1], "wb").write(b"j" * 4000)
        class R: returncode = 0
        return R()

    monkeypatch.setattr(L, "_fetch_thumbnail", no_thumb)
    monkeypatch.setattr(L.subprocess, "run", fake_run)
    new, _ = _place_episode(tmp_path, title="Ep #12", with_image=False)
    season = os.path.dirname(new)
    assert any(n.endswith("-thumb.jpg") for n in os.listdir(season)), os.listdir(season)
    assert calls and calls[0][0] == "ffmpeg"


def test_the_frame_is_taken_past_the_intro_not_at_the_start(monkeypatch, tmp_path):
    # The opening seconds are routinely black, a fade, or a channel intro
    # identical across every episode -- all useless as artwork.
    seeks = []

    def fake_run(cmd, **kw):
        seeks.append(float(cmd[cmd.index("-ss") + 1]))
        open(cmd[-1], "wb").write(b"j" * 4000)
        class R: returncode = 0
        return R()

    monkeypatch.setattr(L, "_fetch_thumbnail", lambda e, d: False)
    monkeypatch.setattr(L.subprocess, "run", fake_run)
    _place_episode(tmp_path, title="Ep #13", with_image=False, duration=600)
    assert seeks and seeks[0] == 60.0          # 10% of a 10-minute video


def test_youtubes_thumbnail_wins_over_a_frame_grab(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(L, "_fetch_thumbnail", lambda e, d: (d.write_bytes(b"y" * 3000), True)[1])
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **k: ran.append(a))
    _place_episode(tmp_path, title="Ep #14", with_image=False)
    assert not ran, "ffmpeg ran even though a YouTube thumbnail was available"


def test_a_failed_extraction_leaves_no_corrupt_image(monkeypatch, tmp_path):
    def fake_run(cmd, **kw):
        open(cmd[-1], "wb").write(b"")           # ffmpeg produced nothing usable
        class R: returncode = 1
        return R()

    monkeypatch.setattr(L, "_fetch_thumbnail", lambda e, d: False)
    monkeypatch.setattr(L.subprocess, "run", fake_run)
    new, _ = _place_episode(tmp_path, title="Ep #15", with_image=False)
    season = os.path.dirname(new)
    assert not any(n.endswith("-thumb.jpg") for n in os.listdir(season))


def test_a_missing_ffmpeg_never_breaks_the_download(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(L, "_fetch_thumbnail", lambda e, d: False)
    monkeypatch.setattr(L.subprocess, "run", boom)
    new, _ = _place_episode(tmp_path, title="Ep #16", with_image=False)
    assert os.path.exists(new)
