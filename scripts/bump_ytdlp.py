"""Bump the exact yt-dlp pins in requirements.txt to their latest PyPI release.

Used by .github/workflows/ytdlp-update.yml. Prints GitHub Actions outputs:
`changed=true|false` and `summary=<pkg old->new, ...>`. Stable releases only
(PyPI's `info.version` skips yt-dlp's nightly .devN builds).
"""

import json
import re
import sys
import urllib.request

PACKAGES = ("yt-dlp", "yt-dlp-ejs")


def latest_version(package: str) -> str:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/json", timeout=30) as resp:
        return json.load(resp)["info"]["version"]


def bump(text: str, latest: dict[str, str]) -> tuple[str, list[str]]:
    """Rewrite `pkg==X` pins to `pkg==latest[pkg]`; return new text + changes.

    Only exact `==` pins at the start of a line are touched, and the extras
    syntax (`yt-dlp[default]==...`) is preserved. A package listed in `latest`
    but not pinned in `text` is an error -- the workflow must not silently
    stop updating it.
    """
    changes = []
    for package, new in latest.items():
        pattern = re.compile(rf"^({re.escape(package)}(?:\[[^\]]*\])?==)(\S+)$", re.MULTILINE)
        match = pattern.search(text)
        if not match:
            raise ValueError(f"{package} is not pinned with == in requirements")
        old = match.group(2)
        if old != new:
            text = pattern.sub(rf"\g<1>{new}", text, count=1)
            changes.append(f"{package} {old} -> {new}")
    return text, changes


def main(path: str) -> None:
    with open(path) as f:
        text = f.read()
    new_text, changes = bump(text, {p: latest_version(p) for p in PACKAGES})
    if changes:
        with open(path, "w") as f:
            f.write(new_text)
    print(f"changed={'true' if changes else 'false'}")
    print(f"summary={', '.join(changes)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "requirements.txt")
