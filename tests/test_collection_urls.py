"""services/downloader.py -- is_probably_collection_url.

Shared between the API's dedupe policy (a collection URL is allowed to
re-run; a single video is not) and the ordering pre-pass (only worth doing
for a collection). Getting this wrong in either direction either lets a
single video re-download forever, or silently skips ordering/matching for
something that actually was a playlist.
"""

import pytest

from services.downloader import is_probably_collection_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123&list=PLxyz",
        "https://youtube.com/playlist?list=PLxyz",
        "https://youtube.com/playlist",
        "https://www.youtube.com/@SomeCreator/videos",
        "https://www.youtube.com/@SomeCreator/shorts",
        "https://www.youtube.com/@SomeCreator/streams",
        "https://www.youtube.com/channel/UCxxxx",
        "https://www.youtube.com/user/somename",
        "https://www.youtube.com/c/somename",
        "https://www.youtube.com/@SomeCreator",
        # Trailing slash shouldn't matter.
        "https://www.youtube.com/@SomeCreator/videos/",
    ],
)
def test_recognised_as_a_collection(url):
    assert is_probably_collection_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123",
        "https://youtu.be/abc123",
        "https://youtu.be/abc123?si=someshareid",
        "https://example.com/some/random/page",
    ],
)
def test_not_recognised_as_a_collection(url):
    assert is_probably_collection_url(url) is False


def test_case_of_the_path_does_not_matter():
    assert is_probably_collection_url("https://www.youtube.com/@Creator/VIDEOS") is True
