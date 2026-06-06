from logging import basicConfig, getLogger, Logger
from os import environ
from nicegui import ui

from controller import register_routes


logger: Logger = getLogger(__name__)


def main():
    log_level: str = environ.get("LOG__LEVEL", "DEBUG")
    log_format: str = environ.get(
        "LOG__FORMAT",
        '%(asctime)s - %(process)s - %(name)-12s - l%(lineno)-3d - %(levelname)-8s - %(message)s'
    )
    basicConfig(level=log_level, format=log_format)

    register_routes(ui)

    host: str = environ.get("UVICORN__HOST", "0.0.0.0")
    port: int = environ.get("UVICORN__PORT", 8080)
    logger.info("start FastAPI server with NiceGUI")
    ui.run(host=host, port=port, reload=False, dark=None, log_config=None)


if __name__ in {"__main__", "__mp_main__"}:
    main()
