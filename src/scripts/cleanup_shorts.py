"""One-off cleanup: remove previously-downloaded YouTube Shorts and their now-empty folders.

Run inside the app container (has DATABASE_URL, MEDIA_ROOT wired up):
    python -m scripts.cleanup_shorts
"""

import asyncio
import shutil
from pathlib import Path

from sqlalchemy import select

from db import Download, MediaItem, async_session
from services.downloader import MEDIA_ROOT


def _is_short(url: str) -> bool:
    return "/shorts/" in url or url.rstrip("/").endswith("/shorts")


async def main() -> None:
    async with async_session() as session:
        items = (await session.execute(select(MediaItem))).scalars().all()
        shorts = [m for m in items if _is_short(m.source_url)]
        print(f"found {len(shorts)} shorts media items (of {len(items)} total)")

        touched_dirs: set[Path] = set()
        for item in shorts:
            # A Source followed at the channel root enqueues the /shorts *tab* as a
            # single Download; yt-dlp downloads it as a playlist, but _on_success only
            # ever records one MediaItem per Download, so local_path is empty here —
            # there is no single file to point at, only the playlist's own folder.
            if not item.local_path:
                print(f"  - {item.title!r}: degenerate playlist-tab entry, no local_path")
            else:
                base = Path(MEDIA_ROOT) / item.local_path
                for candidate in (
                    base,
                    base.with_suffix(".info.json"),
                    *(base.with_suffix(ext) for ext in (".jpg", ".png", ".webp")),
                ):
                    if candidate.exists() and candidate.is_file():
                        candidate.unlink()
                touched_dirs.add(base.parent)

            await session.delete(item)

        # Flush MediaItem deletes before touching Download — media_item.download_id
        # has an FK to download.id, and the two models have no ORM relationship()
        # between them, so the unit of work won't infer the delete order on its own.
        await session.flush()

        download_ids = {item.download_id for item in shorts}
        downloads = (await session.execute(select(Download))).scalars().all()
        for dl in downloads:
            if dl.id in download_ids or _is_short(dl.url):
                await session.delete(dl)

        await session.commit()

    removed_dirs = 0
    for d in touched_dirs:
        try:
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
                removed_dirs += 1
        except OSError:
            pass

    # Catch shorts-tab playlist folders directly on disk, whether or not they ever
    # got a (degenerate) DB record — e.g. yt-dlp names the tab "<Uploader> - Shorts".
    for path in Path(MEDIA_ROOT).rglob("*"):
        if path.is_dir() and path.name.endswith("- Shorts"):
            print(f"  removing orphaned shorts-tab folder: {path}")
            shutil.rmtree(path, ignore_errors=True)
            removed_dirs += 1

    print(f"removed {removed_dirs} now-empty/orphaned folders")


if __name__ == "__main__":
    asyncio.run(main())
