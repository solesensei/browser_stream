import json
import typer
import typing as tp
import textwrap
from pathlib import Path
from browser_stream.echo import echo
import browser_stream.config as config
import secrets
import subprocess
import dataclasses


def prompt(message: str) -> str:
    return typer.prompt(message)


def confirm(message: str, default: bool = True, abort: bool = False) -> bool:
    return typer.confirm(message, default=default, abort=abort)


def prompt_path(message: str) -> Path:
    return typer.prompt(message, type=Path)


def format_list(data: list[str]) -> str:
    return "- " + "\n- ".join(data)


def dedent(text: str) -> str:
    return textwrap.dedent(text).strip()


def generate_token() -> str:
    return secrets.token_hex(16)


def run_process(
    command: list[str],
    exit_on_error: bool = True,
    live_output: bool = False,
    timeout: int | None = None,
) -> subprocess.CompletedProcess:
    command_str = " ".join(command)
    echo.debug(f"Running command: {command_str}")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    stdout_live = ""
    if live_output:
        for line in iter(process.stdout.readline, b""):  # type: ignore
            line = line.decode("utf-8").strip()
            echo.print(line)
            stdout_live += line + "\n"
    try:
        stdout = process.communicate(timeout=timeout)[0].decode()
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

    @classmethod
    def load(cls, path: Path = Path(config.CONFIG_PATH)) -> "Config":
        if not path.exists():
            return Config()
        with open(path, "r") as file:
            data = json.load(file)
        return Config(**data)

    def save(self, path: Path = Path(config.CONFIG_PATH)) -> None:
        echo.debug(f"Saving configuration to {path}")
        if not path.parent.exists():
            path.parent.mkdir(parents=True)
        with open(path, "w") as file:
            json.dump(dataclasses.asdict(self), file, indent=4)
