import json
import typer
import typing as tp
import functools
import textwrap
from pathlib import Path
from browser_stream.echo import echo
import browser_stream.config as config
import secrets
import subprocess
import dataclasses


def prompt(message: str, **kwargs) -> str:
    return typer.prompt(typer.style(message, bold=True), **kwargs)


def confirm(message: str, default: bool = True, abort: bool = False) -> bool:
    return typer.confirm(
        typer.style(f"🤔 {message}", bold=True), default=default, abort=abort
    )


def prompt_path(message: str) -> Path:
    return typer.prompt(typer.style(message, bold=True), type=Path)


def format_list(data: list[str]) -> str:
    return "- " + "\n- ".join(data)


def dedent(text: str) -> str:
    return textwrap.dedent(text).strip()


def generate_token() -> str:
    return secrets.token_hex(16)


def run_process(
    command: list[str],
    input_: str | None = None,
    exit_on_error: bool = True,
    live_output: bool = False,
    timeout: int | None = None,
    sudo: bool = False,
) -> subprocess.CompletedProcess:
    if sudo:
        command = ["sudo"] + command
    command_str = " ".join(command)
    if config.PROMPT_COMMANDS and not confirm(f"Run command: {command_str}"):
        raise ValueError("Aborted")
    echo.debug(f"Running command: {command_str}")
    process = subprocess.Popen(
        command_str if sudo else command,
        stdin=subprocess.PIPE if input_ or sudo else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=sudo,
    )
    stdout_live = ""
    if live_output:
        for line in iter(process.stdout.readline, ""):  # type: ignore
            line = line.strip()
            echo.print(line)
            stdout_live += line + "\n"
    try:
        stdout = process.communicate(
            input=input_,
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


@functools.cache
def _get_sudo_password() -> str:
    return prompt("Enter your sudo password", hide_input=True).strip()


def get_sudo_pass(for_which_command: list[str]) -> str:
    echo.printc("This command requires sudo access", color="yellow")
    echo.print(typer.style("Command: ", bold=True) + " ".join(for_which_command))
    return _get_sudo_password()


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
