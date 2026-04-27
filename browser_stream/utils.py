import dataclasses
import datetime as dt
import functools
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import textwrap
import typing as tp
import urllib.parse
from pathlib import Path

import chardet
import click
import typer

import browser_stream.config as config
from browser_stream.echo import echo

if tp.TYPE_CHECKING:
    from browser_stream.helpers import FfmpegStream


T = tp.TypeVar("T")


def bb(text: str) -> str:
    return typer.style(text, bold=True)


def prompt(message: str, **kwargs) -> str:
    return typer.prompt(bb(message), **kwargs)


def confirm(message: str, default: bool = True, abort: bool = False) -> bool:
    return typer.confirm(bb(f"🤔 {message}"), default=default, abort=abort)


def prompt_audio(audio: "Path | FfmpegStream") -> str:
    if isinstance(audio, Path):
        return prompt(
            f"Enter language for audio file: {audio.name} (eng, esp, ...)"
        ).lower()
    if audio.language:
        return audio.language.lower()
    return prompt(f"Enter language for audio: {audio} (eng, esp, ...)").lower()


def prompt_subtitles(subtitles: "Path | FfmpegStream") -> str:
    if isinstance(subtitles, Path):
        return prompt(
            f"Enter language for subtitles file: {subtitles.name} (eng, esp, ...)"
        ).lower()
    if subtitles.language:
        return subtitles.language.lower()
    return prompt(f"Enter language for subtitles: {subtitles} (eng, esp, ...)").lower()


def get_file_path(
    path: Path,
    codec: str,
    language: str,
    suffix: str = "stream",
) -> Path:
    """abcd.eng.stream.mp4 -> abcd.lang.suffix.codec"""
    language = language.lower()[:2]
    parts = path.stem.replace(f".{suffix}", "").rsplit(".", 1)
    if len(parts) == 1:
        name = parts[0]
    elif len(parts[-1]) <= 3:  # language code
        name = parts[0]
    else:
        name = parts[0] + "." + parts[1]
    return path.with_name(f"{name}.{language}.{suffix}.{codec}")


def resolve_path_pwd(path: Path) -> Path:
    """Resolve path with PWD (shell) if it's relative

    NB! PWD (shell) can be different from the script's directory"""
    path = path.expanduser()
    if path.is_absolute():
        return path
    pwd = Path(os.environ.get("PWD", Path.cwd()))
    resolved_path = pwd / path
    return Path(os.path.normpath(resolved_path))


def prompt_path(message: str, exists: bool = True) -> Path:
    while True:
        path: Path = prompt(message, type=Path)  # type: ignore
        if exists and not path.exists():
            echo.error(f"Path `{path}` does not exist")
            continue
        return path


def select_options_interactive(
    options: tp.Sequence[T],
    option_name: str,
    message: str = "Options:",
) -> tuple[int, T]:
    echo.print(bb(message))
    for i, _option in enumerate(options, start=1):
        echo.print(f"[{i}] {_option}")
    select_i = int(
        typer.prompt(
            bb(f"Select {option_name}"),
            show_choices=False,
            type=click.Choice([str(i) for i in range(1, len(options) + 1)]),
            default="1",
        )
    )
    return select_i - 1, options[select_i - 1]


def select_multi_options(
    options: tp.Sequence[str],
    option_name: str,
    message: str = "Options:",
    defaults: tp.Sequence[int] | None = None,
    allow_none: bool = False,
) -> list[int]:
    """Interactive multi-select from numbered options.

    Args:
        options: Items to display.
        option_name: Label used in the prompt.
        message: Header printed above the list.
        defaults: 0-based indices of pre-selected options (shown with ``*``).
        allow_none: When True, show a ``[0] None`` entry. Typing ``0`` returns ``[]``.

    Returns:
        List of selected 0-based indices (empty list when the user picks "None").
    """
    echo.print(bb(message))
    default_set = set(defaults or [])
    if allow_none:
        none_marker = " *" if not default_set else ""
        echo.print(f"  [0] None{none_marker}")
    for i, _option in enumerate(options):
        marker = " *" if i in default_set else ""
        echo.print(f"  [{i + 1}] {_option}{marker}")

    if allow_none and not default_set:
        default_str = "0"
    elif default_set:
        default_str = ",".join(str(i + 1) for i in sorted(default_set))
    else:
        default_str = "1"

    raw = typer.prompt(
        bb(f"Select {option_name} (comma-separated)"),
        default=default_str,
    )

    # "0" means none when allowed
    if allow_none and raw.strip() == "0":
        return []

    selected: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(options) and idx not in selected:
                selected.append(idx)

    return selected if selected else (sorted(default_set) if default_set else [0])


def get_temp_file(suffix: str = "", create: bool = True) -> Path:
    temp_file = Path(tempfile.mktemp(suffix=suffix, dir=Path.cwd(), prefix=".tmp"))
    if create:
        temp_file.touch()
    return temp_file


def move_file(src: Path, dst: Path, overwrite: bool = False) -> None:
    if not src.is_file():
        raise ValueError("Source is not a file")
    if dst.is_dir():
        dst = dst / src.name
    if src == dst:
        return
    if dst.exists():
        if overwrite:
            # remove file
            dst.unlink()
        else:
            raise FileExistsError(f"File `{dst}` already exists")
    echo.debug(f"Moving file `{src}` to `{dst}`")
    shutil.move(src, dst)


def format_size(size_bytes: int | float) -> str:
    """Format bytes as human-readable string (e.g. 4.3GB)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}PB"


def url_encode(url: str) -> str:
    return urllib.parse.quote(url, safe=":/?&=")


def format_list(data: list[str]) -> str:
    return "- " + "\n- ".join(data)


def dedent(text: str) -> str:
    return textwrap.dedent(text).strip()


def indent(text: str, spaces: int = 4, dedent_: bool = True) -> str:
    if dedent_:
        text = dedent(text)
    return textwrap.indent(text, " " * spaces)


def generate_token() -> str:
    return secrets.token_hex(16)


def run_process(
    command: list[str],
    input_: str | None = None,
    exit_on_error: bool = True,
    live_output: bool = False,
    timeout: int | None = None,
) -> subprocess.CompletedProcess:
    command_str = " ".join(command)
    if config.PROMPT_COMMANDS and not confirm(f"Run command: {command_str}"):
        raise ValueError("Aborted")
    if config.PRINT_CMD:
        echo.print(typer.style("Running command: ", bold=True) + command_str)
    else:
        echo.debug(f"Running command: {command_str}")
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input_ else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    stdout_live = ""
    if live_output:
        for line in iter(process.stdout.readline, ""):  # type: ignore
            line = line.strip()
            echo.print(line)
            stdout_live += line + "\n"
    try:
        stdout = process.communicate(
            input=input_ if input_ else None,
            timeout=timeout,
        )[0]
    except subprocess.TimeoutExpired:
        process.kill()
        echo.debug(f"Command `{command_str}` timed out after {timeout} seconds")
        stdout = ""
        process.returncode = 2
    if exit_on_error and process.returncode != 0:
        raise ValueError(f"Error: {stdout}")
    return subprocess.CompletedProcess(
        args=command,
        returncode=process.returncode,
        stdout=stdout_live if live_output else stdout,
    )


def _get_sudo_password() -> str:
    return prompt("Enter your sudo password", hide_input=True).strip()


@functools.cache
def print_sudo_warning() -> None:
    echo.printc("This command requires sudo access", color="yellow")


def detect_encoding(file_path: Path) -> str:
    with open(file_path, "rb") as f:
        result = chardet.detect(f.read())
    encoding = result["encoding"]
    if encoding is None:
        raise ValueError(f"Could not detect encoding for {file_path}")
    return encoding


def get_sudo_pass(for_which_command: list[str], what_happens: str) -> str:
    print_sudo_warning()
    echo.print(bb("Command: ") + " ".join(for_which_command))
    echo.print(bb("What happens: ") + what_happens)
    return _get_sudo_password()


def parse_duration(duration: str) -> dt.timedelta:
    """01:42:18.05"""
    parts = duration.split(":")
    if "." in parts[-1]:
        _seconds, milliseconds = parts[-1].split(".")
        parts[-1] = _seconds
        milliseconds_i = int(milliseconds)
    else:
        milliseconds_i = 0
    parts_float = list(map(float, parts))
    hours, minutes, seconds = parts_float
    return dt.timedelta(
        hours=hours, minutes=minutes, seconds=seconds, milliseconds=milliseconds_i
    )


@dataclasses.dataclass
class Config:
    media_dir: Path | None = None
    host_url: str | None = None
    plex_port: int | None = None
    nginx_port: int | None = None
    ipv6: bool = False
    ipv4: bool = False
    plex_x_token: str | None = None
    plex_server_id: str | None = None
    nginx_secret: str | None = None
    nginx_conf_name: str | None = None
    nginx_allow_index: bool = False
    nginx_domain_name: str | None = None

    @classmethod
    def load(cls, path: Path = Path(config.CONFIG_PATH)) -> "Config":
        path = resolve_path_pwd(path)
        if not path.exists():
            return Config()
        echo.debug(f"Loading configuration from {path}")
        with open(path) as file:
            data = json.load(file)
        data["media_dir"] = Path(data["media_dir"]) if data["media_dir"] else None
        return Config(**data)

    def save(self, path: Path = Path(config.CONFIG_PATH)) -> None:
        path = resolve_path_pwd(path)
        echo.debug(f"Saving configuration to {path}")
        if not path.parent.exists():
            path.parent.mkdir(parents=True)
        echo.debug(f"Saving configuration to {path}")
        with open(path, "w") as file:
            d = dataclasses.asdict(self)
            d["media_dir"] = d["media_dir"].as_posix() if d["media_dir"] else None
            json.dump(d, file, indent=4)

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["media_dir"] = d["media_dir"].as_posix() if d["media_dir"] else None
        return d
