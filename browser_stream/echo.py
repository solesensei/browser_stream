import functools
import logging
import sys
import typing as tp
import json
import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

from browser_stream import config

logger = logging.getLogger(__name__)


def setup_logger() -> None:
    console = Console(
        theme=Theme(
            {
                "logging.level.info": "bold cyan",
                "logging.level.warn": "bold yellow",
                "logging.level.error": "bold red",
                "logging.level.debug": "bold green",
                "log.time": "bold white",
            }
        ),
        width=150,
        soft_wrap=True,
    )

    class FixedRichHandler(RichHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._log_render.level_width = 5

    handler = FixedRichHandler(
        console=console,
        omit_repeated_times=False,
        show_path=config.DEBUG,
        log_time_format="%X",
    )
    logging.addLevelName(logging.INFO, "info")
    logging.addLevelName(logging.ERROR, "error")
    logging.addLevelName(logging.WARNING, "warn")
    logging.addLevelName(logging.DEBUG, "debug")

    logger = logging.getLogger("browser_stream")
    handler.setLevel(logging.DEBUG if config.DEBUG else logging.INFO)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if config.DEBUG else logging.INFO)


class Echo:
    """Small wrapper around typer.echo"""

    def clear_line(self) -> None:
        if config.JSON_OUTPUT:
            return
        print(" " * 50, end="\r", file=sys.stderr, flush=True)

    def debug(self, msg: str, **kwargs: tp.Any) -> None:
        if config.DEBUG:
            self.clear_line()
            logger.debug(msg)

    def info(self, msg: str, **kwargs: tp.Any) -> None:
        self.clear_line()
        logger.info(msg)

    def warning(self, msg: str, **kwargs: tp.Any) -> None:
        self.clear_line()
        logger.warning(msg)

    def error(self, msg: str, **kwargs: tp.Any) -> None:
        self.clear_line()
        logger.error(msg)

    def print(self, msg: str, **kwargs: tp.Any) -> None:
        self.clear_line()
        typer.echo(msg, **kwargs)

    def printc(
        self, msg: str, color: str | None = None, end: str = "\n", **kwargs: tp.Any
    ) -> None:
        self.clear_line()
        msg = typer.style(msg, fg=color, **kwargs)
        print(msg, end=end, file=sys.stderr, flush=True)

    def print_json(self, data: tp.Any) -> None:
        self.print(json.dumps(data, indent=2, ensure_ascii=False))


echo = Echo()

P = tp.ParamSpec("P")
T = tp.TypeVar("T")


def log(
    message: str,
    color: str | None = None,
    debug: bool = False,
) -> tp.Callable[[tp.Callable[P, T]], tp.Callable[P, T]]:
    def decorator(fn: tp.Callable[P, T]) -> tp.Callable[P, T]:
        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            if config.JSON_OUTPUT or debug and not config.DEBUG:
                return fn(*args, **kwargs)
            echo.printc(f"{message}...", color=color, end="\r")
            result = fn(*args, **kwargs)
            echo.clear_line()
            return result

        return wrapper

    return decorator
