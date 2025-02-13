#!/usr/local/bin/python
import httpx
from pathlib import Path
import shutil
import typing as tp
import functools
import urllib.parse
import dataclasses
import re
import tempfile
import datetime as dt
import typer

import browser_stream.utils as utils
import browser_stream.config as config
from browser_stream.echo import echo


conf = utils.Config.load()


class Exit(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        self.message = message
        self.code = code


@dataclasses.dataclass
class StreamMedia:
    path: Path
    subtitles_burned: bool = False
    subtitle_path: Path | None = None
    subtitle_lang: str | None = None


class PlexAPI:
    """Wrapper around Plex API"""

    def __init__(
        self,
        x_token: str,
        base_url: str = "http://localhost:32400",
        server_id: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._x_token = x_token
        self._server_id = server_id

    @classmethod
    def from_direct_url(cls, direct_url: str) -> "PlexAPI":
        """
        https://192-168-178-47.<server_id>.plex.direct:32400/library/parts/2817/1681580846/file.mkv?download=1&X-Plex-Token=token
        """
        parsed = urllib.parse.urlparse(direct_url)
        server_id = parsed.netloc.split(".")[0]
        x_token = urllib.parse.parse_qs(parsed.query)["X-Plex-Token"][0]
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        return cls(x_token, base_url, server_id)

    def get_direct_url(self, path: str) -> str:
        if self._server_id is None:
            raise Exit("Plex Server ID is not provided")
        return f"{self._base_url}/{path}?X-Plex-Token={self._x_token}"

    @staticmethod
    def encode_url(url: str) -> str:
        return urllib.parse.quote(url, safe="")

    def _request(
        self,
        method: tp.Literal["GET", "POST"],
        path: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, tp.Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
        }
        params = params or {}
        params["X-Plex-Token"] = self._x_token
        response = httpx.request(method, url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    def _get(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, tp.Any]:
        return self._request("GET", path, params)

    # common methods

    def get_libraries(self) -> dict[str, tp.Any]:
        return self._get("/library/sections")["MediaContainer"]

    def get_library(self, section_id: str) -> dict[str, tp.Any]:
        return self._get(f"/library/sections/{section_id}/all")["MediaContainer"]

    def get_metadata(self, id_: str):
        return self._get(f"/library/metadata/{id_}")

    def get_metadata_children(self, id_: str):
        return self._get(f"/library/metadata/{id_}/children")

    def get_streams(self, id_: str):
        return self._get(f"/library/stream/{id_}")

    def do_scan(self, section_id: str, path: str | None = None):
        params = {}
        if path is not None:
            params["path"] = path
        return self._get(f"/library/sections/{section_id}/refresh", params=params)

    # specific methods

    def get_libraries_titles(self) -> list[dict[str, str]]:
        sections = self.get_libraries()
        directories = sections.get("Directory", [])  # type: ignore
        return [
            {
                "title": directory.get("title"),
                "key": directory.get("key"),
            }
            for directory in directories
        ]

    def _get_directory_matched_prefix(self, path: Path) -> dict[str, tp.Any]:
        sections = self.get_libraries()
        directories = sections.get("Directory", [])  # type: ignore
        all_pathes = []
        for directory in directories:
            for location in directory.get("Location", []):
                if path.as_posix().startswith(location["path"]):
                    return directory
                all_pathes.append(location["path"])
        raise Exit(
            f"No library found for path: {path}.\nAvailable pathes:\n{utils.format_list(all_pathes)}"
        )

    def _get_media_key_from_directory(self, key: str, path: Path) -> str:
        for title_metadata in self.get_library(key)["Metadata"]:
            for media in title_metadata.get("Media", []):
                for part in media.get("Part", []):
                    if path.as_posix() == part["file"]:
                        return title_metadata["ratingKey"]
        raise Exit(f"No media found for path: {path}, directory key: {key}")

    def get_library_id_by_path(self, path: Path) -> str:
        directory = self._get_directory_matched_prefix(path)
        key = directory["key"]
        media_key = self._get_media_key_from_directory(key, path)

    def get_stream_url(self, path: Path) -> str:
        key = self.get_library_id_by_path(path)
        return self.get_direct_url(f"/library/metadata/{key}/media/0/file.mkv")


class Nginx:
    """Wrapper around nginx command"""

    def __init__(self) -> None:
        self._cmd = "nginx"

    @functools.cache
    def exit_if_not_installed(self):
        if shutil.which(self._cmd) is None:
            raise Exit(f"'{self._cmd}' is not found in PATH", code=2)

    def _run(self, *args: tp.Any, what_happens: str, exit_on_error: bool = True) -> str:
        self.exit_if_not_installed()
        cmd = ["sudo", "-S", self._cmd, *map(str, args)]
        password = utils.get_sudo_pass(cmd, what_happens=what_happens)
        return utils.run_process(
            cmd,
            exit_on_error=exit_on_error,
            input_=password,
        ).stdout

    def test(self):
        echo.info("Testing nginx configuration")
        return self._run("-t", what_happens="Nginx configuration would be tested")

    def reload(self):
        echo.info("Reloading nginx configuration")
        return self._run(
            "-s", "reload", what_happens="Nginx configuration would be reloaded"
        )

    def get_browser_stream_config(
        self,
        media_path: Path,
        secret: str,
        port: int = 32000,
        ipv6: bool = False,
        ipv4: bool = False,
        allow_index: bool = False,
        ssl: bool = False,
        server_name: str | None = None,
    ) -> str:
        ssl_certificate = Path(f"/etc/letsencrypt/live/{server_name}/fullchain.pem")
        ssl_certificate_key = Path(f"/etc/letsencrypt/live/{server_name}/privkey.pem")

        if ssl and not server_name:
            raise Exit("Server name is required for SSL configuration")

        ssl_config = (
            utils.indent(
                f"""
                ssl_certificate {ssl_certificate};
                ssl_certificate_key {ssl_certificate_key};
                ssl_protocols TLSv1.2 TLSv1.3;
                ssl_ciphers HIGH:!aNULL:!MD5;

                # Redirect HTTP to HTTPS
                if ($scheme != "https") {{
                    return 301 https://$host$request_uri;
                }}
                """,
                spaces=16,
            )
            if ssl
            else ""
        )

        listen_ipv6 = (
            (f"listen [::]:{port} ssl;" if ssl else f"listen [::]:{port};")
            if ipv6
            else ""
        )
        listen_ipv4 = (
            (f"listen {port} ssl;" if ssl else f"listen {port};") if ipv4 else ""
        )

        return utils.dedent(f"""
            server {{
                {listen_ipv4}
                {listen_ipv6}
                server_name {server_name or "_"};

{ssl_config}

                # Block root access
                location = / {{
                    return 403;
                }}

                # Serve media files
                location /media/ {{
                    alias "{media_path.as_posix()}/";
                    autoindex {"on" if allow_index else "off"};

                    # Secure with token authentication
                    set $allow_access 0;
                    set $secret "{secret}";
                    if ($arg_x-token = $secret) {{
                        set $allow_access 1;
                    }}
                    if ($allow_access = 0) {{
                        return 403;
                    }}

                    types {{
                        video/mp4 mp4;
                        text/html html;
                        text/vtt vtt;
                    }}
                    default_type application/octet-stream;
                }}
            }}
        """)


@dataclasses.dataclass
class FfmpegStream:
    index: int
    type: tp.Literal["video", "audio", "subtitle"]
    codec: str
    title: str | None = None
    encoding_info: str | None = None
    language: str | None = None

    def __repr__(self) -> str:
        t = f"{self.title} ({self.codec})"
        if self.language:
            t += f" [{self.language}]"
        return t


@dataclasses.dataclass
class FfmpegMediaInfo:
    filename: Path
    title: str
    bitrate: str
    duration: dt.timedelta | None
    streams: list[FfmpegStream]

    def __repr__(self) -> str:
        return f"{self.title} ({self.filename})"

    @property
    def video(self) -> FfmpegStream:
        video_ = next((s for s in self.streams if s.type == "video"), None)
        assert video_ is not None, "Video stream not found"
        return video_

    @property
    def audios(self) -> list[FfmpegStream]:
        return [s for s in self.streams if s.type == "audio"]

    @property
    def subtitles(self) -> list[FfmpegStream]:
        return [s for s in self.streams if s.type == "subtitle"]

    @classmethod
    def parse(cls, output: str) -> "FfmpegMediaInfo":
        lines = output.splitlines()

        filename: Path = Path()
        title: str = ""
        bitrate: str = ""
        duration: dt.timedelta = dt.timedelta()
        last_stream_info: FfmpegStream | None = None
        streams: list[FfmpegStream] = []

        for i, line in enumerate(lines):
            line = line.strip()
            if "from" in line:
                match = re.search(r"from '(.+)'", line)
                if match:
                    filename = Path(match.group(1))
                else:
                    echo.warning(f"Cannot parse filename from line: {line}")
            if "Duration" in line:
                match = re.search(r"Duration: (.+?),", line)
                if match:
                    if match.group(1) != "N/A":
                        duration = utils.parse_duration(match.group(1))
                else:
                    echo.warning(f"Cannot parse duration from line: {line}")
            if line.startswith("title") and last_stream_info is None:
                match = re.search(r"title\s+:\s+(.+)", line)
                if match:
                    title = match.group(1)
                else:
                    echo.warning(f"Cannot parse title from line: {line}")
            if "bitrate" in line:
                match = re.search(r"bitrate:\s+(.+)", line)
                if match:
                    if match.group(1) != "N/A":
                        bitrate = match.group(1)
                else:
                    echo.warning(f"Cannot parse bitrate from line: {line}")
            if "Stream" in line:
                if last_stream_info:
                    streams.append(last_stream_info)
                match = re.search(
                    r"Stream #\d+:(\d+)(?:\((\w+)\))?: (\w+): (\w+)(.+)",
                    line,
                )
                if match:
                    index, lang, type_, codec, encoding_info = match.groups()
                    last_stream_info = FfmpegStream(
                        index=int(index),
                        type=type_.lower(),  # type: ignore
                        codec=codec,
                        language=lang,
                        encoding_info=encoding_info.split(",", 1)[-1].strip(),
                    )
                else:
                    echo.warning(f"Cannot parse stream info from line: {line}")
            if line.startswith("title") and last_stream_info:
                match = re.search(r"title\s+:\s+(.+)", line)
                if match:
                    last_stream_info.title = match.group(1)
                else:
                    echo.warning(f"Cannot parse stream title from line: {line}")

        if last_stream_info:
            streams.append(last_stream_info)

        return cls(
            filename=filename,
            title=title,
            bitrate=bitrate,
            duration=duration,
            streams=streams,
        )

    def to_dict(self) -> dict[str, tp.Any]:
        d = dataclasses.asdict(self)
        d["duration"] = str(d["duration"])
        d["filename"] = d["filename"].as_posix()
        return d


class Ffmpeg:
    """Wrapper around ffmpeg command"""

    def __init__(self) -> None:
        self._cmd = "ffmpeg"

    @functools.cache
    def exit_if_not_installed(self):
        if shutil.which(self._cmd) is None:
            raise Exit(f"'{self._cmd}' is not found in PATH", code=2)

    def _run(self, *args: tp.Any, **kwargs) -> str:
        self.exit_if_not_installed()
        cmd = [self._cmd, *map(str, args)]
        return utils.run_process(cmd, **kwargs).stdout

    @functools.cache
    def get_media_info(self, path: Path) -> FfmpegMediaInfo:
        res = self._run(
            "-i",
            path.resolve(),
            "-hide_banner",
            exit_on_error=False,
        )
        return FfmpegMediaInfo.parse(res)

    def print_media_info(self, path: Path) -> FfmpegMediaInfo:
        media_file_info = self.get_media_info(path)
        echo.info("Media info:")
        echo.print(utils.bb("Filename: ") + media_file_info.filename.as_posix())
        echo.print(utils.bb("Title: ") + media_file_info.title)
        echo.print(utils.bb("Bitrate: ") + media_file_info.bitrate)
        echo.print(utils.bb("Duration: ") + str(media_file_info.duration))
        echo.print("-" * 50)
        video = media_file_info.video
        echo.print(utils.bb("Video: ") + f"({video.codec}) {video.title}")
        for i, audio in enumerate(media_file_info.audios):
            echo.print(
                utils.bb(f"Audio {i} [{audio.language}]: ")
                + f"({audio.codec}) {audio.title}"
            )
        for i, subtitle in enumerate(media_file_info.subtitles):
            echo.print(
                utils.bb(f"Subtitle {i} [{subtitle.language}]: ")
                + f"({subtitle.codec}) {subtitle.title}"
            )
        return media_file_info

    def extract_subtitle(
        self, media_file: Path, stream_index: int, subtitle_lang: str | None
    ) -> Path:
        media_file_info = self.get_media_info(media_file)
        if stream_index >= len(media_file_info.streams):
            echo.print_json(media_file_info.to_dict())
            raise Exit(f"Stream index out of range: {stream_index}")
        subtitle = next(
            (s for s in media_file_info.subtitles if s.index == stream_index), None
        )
        if subtitle is None:
            subtitle_streams = [s.index for s in media_file_info.subtitles]
            raise Exit(
                f"Stream not found: {stream_index}. Available subtitles streams: {subtitle_streams}"
            )
        if (
            subtitle_lang
            and subtitle.language
            and subtitle.language[:2] != subtitle_lang[:2]
        ):
            echo.warning(
                f"Subtitle language mismatch: {subtitle.language} != {subtitle_lang}"
            )
        subtitle_lang = subtitle.language or subtitle_lang or "eng"
        echo.info(
            f"Extracting subtitle: {subtitle.title} [{subtitle_lang}] from {media_file}"
        )
        subtitle_file = media_file.with_suffix(f".{subtitle_lang}.{subtitle.codec}")
        if subtitle_file.exists():
            if utils.confirm(
                f"Subtitle file already exists: {subtitle_file.name}. Do you want to overwrite it?"
            ):
                subtitle_file.unlink()
            else:
                return subtitle_file
        self._run(
            "-i",
            media_file,
            "-map",
            f"0:{stream_index}",
            subtitle_file,
            live_output=True,
        )
        return subtitle_file

    def convert_to_mp4(
        self,
        media_file: Path,
        output_file: Path,
        audio_lang: str | None = None,
        audio_file: Path | None = None,
        audio_stream: int | None = None,
        subtitle_file: Path | None = None,
        subtitle_lang: str | None = None,
        burn_subtitles: bool = False,
    ) -> Path:
        """
        Convert media file to mp4 format

        Args:
            media_file: Path to media file
            audio_lang: Audio language
            audio_file: Path to audio file
            audio_stream: Audio stream index
            subtitle_file: Path to subtitle file
            subtitle_lang: Subtitle language
            burn_subtitles: Burn subtitles into video (default: False)
        """
        echo.info(f"Converting media file: {media_file} to MP4 format")
        args = [
            "-i",
            media_file,
            "-c:v",
            "copy",
            "-y",
            output_file,
        ]
        if audio_file is not None:
            if audio_stream is not None:
                args.extend(["-map", f":a:{audio_stream}", audio_file])
            elif audio_lang is not None:
                args.extend(["-map", f":a:{audio_lang}", audio_file])
        if subtitle_file is not None:
            if burn_subtitles:
                args.extend(["-vf", f"subtitles={subtitle_file}"])
            else:
                args.extend(["-i", subtitle_file, "-c:s", "mov_text"])
            if subtitle_lang:
                args.extend(["-metadata:s:s:0", f"language={subtitle_lang}"])
        self._run(*args, live_output=True)
        return output_file

    def convert_subtitle_to_vtt(self, subtitle_file: Path) -> Path:
        echo.info(f"Converting subtitle file: {subtitle_file} to VTT format")
        output_file = subtitle_file.with_suffix(".vtt")
        self._run(
            "-i",
            subtitle_file,
            "-c:s",
            "webvtt",
            "-y",
            output_file,
            live_output=True,
        )
        return output_file

    def convert_audio_to_aac(self, audio_file: Path) -> Path:
        echo.info(f"Converting audio file: {audio_file} to AAC format")
        output_file = audio_file.with_suffix(".aac")
        self._run(
            "-i",
            audio_file,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-y",
            output_file,
            live_output=True,
        )
        return output_file


class HTML:
    @staticmethod
    def get_video_html_with_subtitles(
        video_url: str,
        subtitles_url: str,
        language: str = "English",
    ) -> str:
        language = language.capitalize()
        srclang = language.lower()[0:2]
        return utils.dedent(f"""
            <video controls style="position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; object-fit: cover;">
                <source src="{video_url}" type="video/mp4">
                <track src="{subtitles_url}" kind="subtitles" srclang="{srclang}" label="{language}">
                Your browser does not support the video tag.
            </video>
        """)


class FS:
    """Filesystem utility functions"""

    @staticmethod
    def get_extension(path: Path) -> str:
        return path.suffix.lstrip(".")

    @staticmethod
    def create_dir(path: Path, sudo: bool = False):
        if path.exists():
            return
        echo.info(f"Creating directory: {path}")
        if sudo:
            command = ["sudo", "-S", "mkdir", "-p", path.as_posix()]
            password = utils.get_sudo_pass(
                command, what_happens="Directory would be created"
            )
            utils.run_process(command, input_=password)
        else:
            path.mkdir(parents=True)

    @staticmethod
    def write_file(path: Path, content: str, sudo: bool = False):
        echo.info(f"Creating file: {path}")
        if sudo:
            with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
                tmp.write(content + "\n")
            command = ["sudo", "-S", "mv", tmp.name, path.as_posix()]
            password = utils.get_sudo_pass(
                command, what_happens="File would be created"
            )
            utils.run_process(command, input_=password)
        else:
            with path.open("w") as f:
                f.write(content + "\n")

    @staticmethod
    def create_symlink(src: Path, dst: Path, sudo: bool = False):
        if src.exists() and not src.is_symlink():
            raise Exit(f"Sorce path is not a symlink: {src}")
        if not dst.exists():
            raise Exit(f"Destination path does not exist: {dst}")
        echo.info(f"Creating symlink: {src} -> {dst}")
        if sudo:
            command = ["sudo", "-S", "ln", "-sf", dst.as_posix(), src.as_posix()]
            password = utils.get_sudo_pass(
                command, what_happens="Symlink would be created"
            )
            utils.run_process(command, input_=password)
        else:
            src.symlink_to(dst)

    @staticmethod
    def remove_symlink(path: Path, sudo: bool = False):
        if not path.exists():
            return
        if not path.is_symlink():
            raise Exit(f"Path is not a symlink: {path}")
        echo.info(f"Removing symlink: {path}")
        if sudo:
            command = ["sudo", "-S", "rm", path.as_posix()]
            password = utils.get_sudo_pass(
                command, what_happens="Symlink would be removed"
            )
            utils.run_process(command, input_=password)
        else:
            path.unlink()

    @staticmethod
    def remove_file(path: Path, sudo: bool = False):
        if not path.exists():
            return
        echo.info(f"Removing file: {path}")
        if sudo:
            command = ["sudo", "-S", "rm", path.as_posix()]
            password = utils.get_sudo_pass(
                command, what_happens="File would be removed"
            )
            utils.run_process(command, input_=password)
        else:
            path.unlink()

    @staticmethod
    def read_file(path: Path) -> str:
        with path.open() as f:
            return f.read()


def build_stream_url_nginx(
    media_file: Path,
) -> str:
    """Build stream URL for media file using Nginx server"""
    assert conf.nginx_secret, "Nginx secret not found"
    assert conf.host_url, "Host URL not found"
    assert conf.nginx_port, "Nginx port not found"
    return f"http://{conf.host_url}:{conf.nginx_port}/media/{media_file.as_posix()}?x-token={conf.nginx_secret}"


def build_stream_url_plex(
    media_file: Path,
) -> str:
    """Build stream URL for media file using Plex server"""
    assert conf.plex_x_token, "Plex X-Token not found"
    assert conf.host_url, "Host URL not found"
    assert conf.plex_server_id, "Plex server ID not found"
    plex = PlexAPI(conf.plex_x_token, conf.host_url, server_id=conf.plex_server_id)
    return plex.get_stream_url(media_file)


def select_audio(
    media_file: Path,
    audio_file: Path | None = None,
    audio_lang: str | None = None,
) -> Path | FfmpegStream:
    ffmpeg = Ffmpeg()
    media_file_info = ffmpeg.get_media_info(media_file)
    audios = media_file_info.audios
    if audio_file:
        echo.info(f"Using audio file: {audio_file}")
        audio_file_info = ffmpeg.get_media_info(audio_file)
        audio = audio_file_info.audios[0]

        if audio.codec not in config.BROWSER_AUDIO_CODECS:
            audio_file_aac = audio_file.with_suffix(".aac")
            if audio_file_aac.exists() and utils.confirm(
                f"AAC audio file already exists: {audio_file_aac}. Do you want to use it?"
            ):
                return audio_file_aac
            if utils.confirm(
                f"Audio codec is not AAC: {audio.codec}. Do you want to convert it?"
            ):
                audio_file = ffmpeg.convert_audio_to_aac(audio_file)

        if audio_lang and audio.language and audio.language[:2] != audio_lang[:2]:
            echo.warning(
                f"Audio language mismatch: {audio.language} != {audio_lang}. Using audio file"
            )
        return audio_file

    if audio_lang:
        matched_audios = [
            a for a in audios if a.language and a.language[:2] == audio_lang[:2]
        ]
        if not matched_audios:
            echo.warning(f"No audio found for language: {audio_lang}")
            select_audios_from = audios
        elif len(matched_audios) == 1:
            echo.info(f"Selected audio: {matched_audios[0].title}")
            return matched_audios[0]
        else:
            select_audios_from = matched_audios

    else:
        select_audios_from = audios

    echo.print("-" * 50)
    index, _ = utils.select_options_interactive(
        [f"[{a.language}] {a.title} ({a.codec})" for a in select_audios_from],
        option_name="Audio",
        message="Select audio stream",
    )
    selected_audio = select_audios_from[index]
    if selected_audio.codec not in config.BROWSER_AUDIO_CODECS:
        audio_aac = media_file.with_suffix(".aac")
        if audio_aac.exists() and utils.confirm(
            f"AAC audio file already exists: {audio_aac.name}. Do you want to use it?"
        ):
            return audio_aac
        if utils.confirm(
            f"Audio codec is not AAC: {selected_audio.codec}. Do you want to convert it?"
        ):
            return ffmpeg.convert_audio_to_aac(media_file_info.filename)
    return selected_audio


def select_subtitle(
    media_file: Path,
    subtitle_file: Path | None = None,
    subtitle_lang: str | None = None,
    burn_subtitles: bool = False,
) -> tuple[Path | None, str | None]:
    ffmpeg = Ffmpeg()
    fs = FS()
    media_file_info = ffmpeg.get_media_info(media_file)
    subtitles = media_file_info.subtitles
    select_subtitles_from: list[FfmpegStream] | None = None
    selected_subtitle: int | None = None
    if subtitle_file:
        echo.info(f"Using subtitle file: {subtitle_file}")
        subtitle_file_info = ffmpeg.get_media_info(subtitle_file)
        subtitle = subtitle_file_info.subtitles[0]
        if (
            subtitle_lang
            and subtitle.language
            and subtitle.language[:2] != subtitle_lang[:2]
        ):
            echo.warning(
                f"Subtitle language mismatch: {subtitle.language} != {subtitle_lang}. Using subtitle file"
            )
    elif subtitle_lang:
        matched_subtitles = [
            s for s in subtitles if s.language and s.language[:2] == subtitle_lang[:2]
        ]
        if not matched_subtitles:
            echo.warning(f"No subtitle found for language: {subtitle_lang}")
            select_subtitles_from = subtitles
        elif len(matched_subtitles) == 1:
            echo.info(f"Selected subtitle: {matched_subtitles[0].title}")
            selected_subtitle = matched_subtitles[0].index
            subtitle_lang = matched_subtitles[0].language
        else:
            select_subtitles_from = matched_subtitles
    elif subtitles and utils.confirm("Do you want to select subtitle stream?"):
        select_subtitles_from = subtitles
    else:
        return None, None

    if select_subtitles_from:
        echo.print("-" * 50)
        index, _ = utils.select_options_interactive(
            [f"[{s.language}] {s.title} ({s.codec})" for s in select_subtitles_from],
            option_name="Subtitle",
            message="Select subtitle stream",
        )
        selected_subtitle = select_subtitles_from[index].index
        subtitle_lang = select_subtitles_from[index].language

    if selected_subtitle is not None:
        subtitle_file = ffmpeg.extract_subtitle(
            media_file, selected_subtitle, subtitle_lang
        )

    assert subtitle_file is not None, "Subtitle file not found"

    if not burn_subtitles and fs.get_extension(subtitle_file) != ".vtt":
        vtt_subtitle_file = subtitle_file.with_suffix(".vtt")
        if vtt_subtitle_file.exists():
            echo.info(
                f"VTT subtitle file already exists: {vtt_subtitle_file}. Using it, instead of {subtitle_file}"
            )
            subtitle_file = vtt_subtitle_file
        elif utils.confirm(
            f"Subtitle file is not in VTT format: {subtitle_file.name} (supported in HTML5). Do you want to convert it?"
        ):
            subtitle_file = ffmpeg.convert_subtitle_to_vtt(subtitle_file)

    return subtitle_file, subtitle_lang


def prepare_file_to_stream(
    media_file: Path,
    audio_file: Path | None = None,
    audio_lang: str | None = None,
    subtitle_file: Path | None = None,
    subtitle_lang: str | None = None,
    burn_subtitles: bool = False,
) -> StreamMedia:
    fs = FS()
    ffmpeg = Ffmpeg()

    ffmpeg.print_media_info(media_file)
    selected_audio = select_audio(
        media_file=media_file,
        audio_file=audio_file,
        audio_lang=audio_lang,
    )
    subtitle_file, subtitle_lang = select_subtitle(
        media_file=media_file,
        subtitle_file=subtitle_file,
        subtitle_lang=subtitle_lang,
        burn_subtitles=burn_subtitles,
    )

    if burn_subtitles and not subtitle_file:
        raise Exit("Subtitles not found for burning")

    if fs.get_extension(media_file) != ".mp4":
        output_file = media_file.with_suffix(".mp4")
        if output_file.exists() and not utils.confirm(
            f"File already exists: {output_file}, do you want to overwrite it?"
        ):
            echo.warning("Skipping conversion. File already exists")
            media_file = output_file
        elif utils.confirm(f"Convert {media_file.name} to MP4", abort=True):
            media_file = ffmpeg.convert_to_mp4(
                media_file,
                output_file,
                audio_file=selected_audio if isinstance(selected_audio, Path) else None,
                audio_stream=selected_audio.index
                if isinstance(selected_audio, FfmpegStream)
                else None,
                audio_lang=audio_lang
                or (
                    selected_audio.language
                    if isinstance(selected_audio, FfmpegStream)
                    else None
                ),
                subtitle_file=subtitle_file,
                subtitle_lang=subtitle_lang,
                burn_subtitles=burn_subtitles,
            )

    return StreamMedia(
        path=media_file,
        subtitles_burned=burn_subtitles,
        subtitle_path=subtitle_file,
        subtitle_lang=subtitle_lang,
    )


def stream_nginx(
    media_file: Path,
    audio_file: Path | None = None,
    audio_lang: str | None = None,
    subtitle_file: Path | None = None,
    subtitle_lang: str | None = None,
    burn_subtitles: bool = False,
    do_not_convert: bool = False,
):
    """
    Check Nginx configuration, convert file and prints the URL to stream media file
    """
    fs = FS()
    nginx = Nginx()
    ffmpeg = Ffmpeg()
    html = HTML()

    if not conf.nginx_secret:
        raise typer.BadParameter(
            "Nginx configuration not found, run `browser-streamer nginx` first"
        )
    if not conf.media_dir:
        raise typer.BadParameter(
            "Media directory not found, run `browser-streamer nginx` first"
        )
    if not media_file.as_posix().startswith(conf.media_dir.as_posix()):
        raise typer.BadParameter(
            f"Media file must be in media directory: {conf.media_dir}",
            param_hint="media-file",
        )

    if conf.nginx_allow_index:
        echo.warning(
            "Directory listing is enabled in Nginx configuration (allow_index=true). That means anyone can navigate through your media files"
        )

    if not do_not_convert:
        stream_media = prepare_file_to_stream(
            media_file=media_file,
            audio_file=audio_file,
            audio_lang=audio_lang,
            subtitle_file=subtitle_file,
            subtitle_lang=subtitle_lang,
            burn_subtitles=burn_subtitles,
        )
        media_file = stream_media.path
        subtitle_file = stream_media.subtitle_path
        subtitle_lang = stream_media.subtitle_lang

    if subtitle_file and not burn_subtitles:
        echo.info(
            f"Create HTML file with video and subtitles: {media_file.with_suffix('.html')}"
        )
        html_data = html.get_video_html_with_subtitles(
            video_url=build_stream_url_nginx(media_file),
            subtitles_url=build_stream_url_nginx(subtitle_file),
            language=subtitle_lang or "Unknown",
        )
        media_file = media_file.with_suffix(".html")
        fs.write_file(media_file, html_data)

    echo.info("Preparation done")
    echo.info(
        f"Stream media file using Nginx server: {build_stream_url_nginx(media_file)}"
    )


def stream_plex(
    media_file: Path,
    audio_lang: str | None = None,
    subtitle_file: Path | None = None,
    subtitle_lang: str | None = None,
    burn_subtitles: bool = False,
    do_not_convert: bool = False,
):
    """
    Check file exists on Plex server, convert file and prints the URL to stream media file
    """
    fs = FS()
    ffmpeg = Ffmpeg()
    html = HTML()

    if not conf.plex_x_token:
        raise typer.BadParameter(
            "Plex X-Token not found, run `browser-streamer plex` first"
        )
