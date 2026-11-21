"""services/cookies_health.py -- same login test as yt-dlp's _has_auth_cookies."""

from services.cookies_health import is_logged_in, is_logged_in_text, youtube_cookie_names

FUTURE, PAST = 4102444800, 1000000000  # 2100, 2001


def _line(name, expires=FUTURE, domain=".youtube.com", httponly=False):
    prefix = "#HttpOnly_" if httponly else ""
    return f"{prefix}{domain}\tTRUE\t/\tTRUE\t{expires}\t{name}\tvalue"


def _file(*lines):
    return "# Netscape HTTP Cookie File\n" + "\n".join(lines) + "\n"


def test_login_info_and_sapisid_is_logged_in():
    assert is_logged_in_text(_file(_line("LOGIN_INFO", httponly=True), _line("SAPISID")))


def test_any_sid_cookie_variant_counts():
    assert is_logged_in_text(_file(_line("LOGIN_INFO"), _line("__Secure-3PAPISID")))


def test_rotated_file_without_login_info_is_not_logged_in():
    # YouTube clears LOGIN_INFO on rotation but leaves 3PAPISID behind.
    assert not is_logged_in_text(_file(_line("__Secure-3PAPISID"), _line("SAPISID")))


def test_expired_cookies_do_not_count():
    assert not is_logged_in_text(_file(_line("LOGIN_INFO", expires=PAST), _line("SAPISID")))


def test_session_cookie_counts():
    assert is_logged_in_text(_file(_line("LOGIN_INFO", expires=0), _line("SAPISID")))


def test_other_domains_are_ignored():
    assert not is_logged_in_text(_file(_line("LOGIN_INFO", domain=".google.com"), _line("SAPISID", domain=".google.com")))


def test_comments_and_junk_are_ignored():
    assert youtube_cookie_names("# comment\n\nnot a cookie line\n") == set()


def test_missing_file_is_not_logged_in(tmp_path):
    assert not is_logged_in(tmp_path / "nope.txt")
