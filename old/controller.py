from logging import getLogger, Logger
from nicegui import ui
import asyncio

from service import download_video_or_playlist


logger: Logger = getLogger(__name__)


def register_routes(ui_instance):
    @ui_instance.page('/')
    def index():
        ui.label('YouTube Video/Playlist Downloader')
        url_input = ui.input('Enter video or playlist URL').props('autofocus')
        status_label = ui.label('')

        with ui.linear_progress(
            value=0.0,
            show_value=False,
            size='30px'
        ).style('width: 100%') as progress:
            ui.label().classes('absolute-center text-sm').bind_text_from(
                progress,
                'value',
                backward=lambda v: f'{v*100:.0f}%' if v is not None else ''
            )
        progress.visible = False
        progress_queue = asyncio.Queue(maxsize=10)

        async def update_progress():
            while True:
                progress_val = await progress_queue.get()
                progress.value = progress_val
                if progress_val >= 1.0:
                    progress.visible = False
                    break
                else:
                    progress.visible = True

        async def on_submit():
            url = url_input.value.strip()
            if not url:
                status_label.set_text('Please enter a valid URL.')
                return

            status_label.set_text('Downloading, please wait...')
            progress.value = 0.0
            progress.visible = True

            updater_task = asyncio.create_task(update_progress())

            try:
                result_files = await download_video_or_playlist(url, progress_queue)
                await updater_task
                if len(result_files) == 1:
                    status_label.set_text(f'Download complete: {result_files[0]}')
                else:
                    status_label.set_text(f'Download complete: {len(result_files)} files')
            except Exception as e:
                status_label.set_text(f'Error: {str(e)}')
                progress.visible = False

        ui.button('Download', on_click=on_submit)
