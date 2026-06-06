import asyncio
from logging import getLogger, Logger
from os import getenv
from os.path import abspath
from typing import Optional
from yt_dlp import YoutubeDL


logger: Logger = getLogger(__name__)


progress_queue: Optional[asyncio.Queue[float]] = None
current_video_index = 0
total_videos = 1


def progress_hook(d):
    global current_video_index, total_videos

    if d['status'] == 'downloading':
        total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
        downloaded_bytes = d.get('downloaded_bytes')
        if total_bytes and downloaded_bytes:
            current_video_progress = downloaded_bytes / total_bytes
            overall_progress = (current_video_index + current_video_progress) / total_videos
            if progress_queue:
                try:
                    progress_queue.put_nowait(overall_progress)
                except asyncio.QueueFull:
                    pass
    elif d['status'] == 'finished':
        current_video_index += 1
        if progress_queue:
            try:
                progress_queue.put_nowait(current_video_index / total_videos)
            except asyncio.QueueFull:
                pass


def get_yt_dlp_options():
    archive_file: str = getenv("YT_DLP__ARCHIVE_FILE")
    cookies_path = getenv('YT_DLP_COOKIES_PATH')

    ydl_opts = {
        'outtmpl': 'downloads/%(channel)s/%(playlist_title)s/%(title)s.%(ext)s',
        'quiet': False,
        'verbose': True,
        'no_warnings': True,
        'download_archive': archive_file,
        'progress_hooks': [progress_hook],
        'logger': logger,
    }

    if cookies_path:
        ydl_opts['cookiefile'] = abspath(cookies_path)
        logger.debug(f"use cookiefile: {ydl_opts['cookiefile']}")
    else:
        logger.debug(f"not using any cookies file")

    return ydl_opts


async def download_video_or_playlist(url: str, progress_q: asyncio.Queue):
    global progress_queue, current_video_index, total_videos
    progress_queue = progress_q

    current_video_index = 0
    total_videos = 1

    ydl_opts = get_yt_dlp_options()

    from asyncio import to_thread

    def blocking_download():
        global total_videos
        with YoutubeDL(ydl_opts) as ydl:
            logger.info(f"{url} > start download")
            info = ydl.extract_info(url, download=False)
            logger.debug(info)

            if 'entries' in info:
                total_videos = len(info['entries'])
            else:
                total_videos = 1

            downloaded_files = []

            info = ydl.extract_info(url, download=True)
            if 'entries' in info:
                for entry in info['entries']:
                    filename = ydl.prepare_filename(entry)
                    downloaded_files.append(filename)
                    logger.info(f"video downloaded: {filename}")
            else:
                filename = ydl.prepare_filename(info)
                downloaded_files.append(filename)
                logger.info(f"video downloaded: {filename}")

            logger.info(f"{url} > download finished")
            return downloaded_files

    result = await to_thread(blocking_download)
    progress_queue = None
    return result
