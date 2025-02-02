import os

__version__ = "0.1.0"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).lower()
    return value in ["true", "1", "yes"]


# Typer
CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

# Environment variables
DEBUG = _env_flag("DEBUG")
PRETTY_EXCEPTIONS = _env_flag("PRETTY_EXCEPTIONS", default=True)
RAISE_EXCEPTIONS = _env_flag("RAISE_EXCEPTIONS")
JSON_OUTPUT = _env_flag("JSON_OUTPUT")
PROMPT_COMMANDS = _env_flag("PROMPT_COMMANDS")


# Constants
CONFIG_PATH = "~/.browser_stream/config.json"
