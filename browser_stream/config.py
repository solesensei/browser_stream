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
PRINT_CMD = _env_flag("PRINT_CMD")
BROWSER_AUDIO_CODEC = os.getenv("BROWSER_AUDIO_CODEC", "aac").lower()
BROWSER_AUDIO_BITRATE = os.getenv("BROWSER_AUDIO_BITRATE", "192k")
FFPEG_ENCODE_CRF = os.getenv("FFPEG_ENCODE_CRF", "20")
FFPEG_ENCODE_PRESET = os.getenv("FFPEG_ENCODE_PRESET", "fast")
VIDEO_EXTENSIONS = {
    "mp4",
    "mkv",
    "avi",
    "mov",
    "webm",
    "flv",
    "wmv",
    "m4v",
    "3gp",
    "ts",
}
AUDIO_EXTENSIONS = {
    "mp3",
    "m4a",
    "aac",
    "flac",
    "wav",
    "wma",
    "mka",
}
SUBTITLE_EXTENSIONS = {"srt", "ssa", "ass", "vtt"}
FS_MAX_DIRS = int(os.getenv("FS_MAX_DIRS", "10"))

# Constants
CONFIG_PATH = os.path.expanduser("~/.browser_stream/config.json")
