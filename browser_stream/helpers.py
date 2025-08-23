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


class Exit(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        self.message = message
        self.code = code


def exit_if(condition: tp.Any, message: str, code: int = 1) -> None:
    """Helper method to exit if condition is truthy"""
    if condition:
        raise Exit(message, code)


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
        return self._get_media_key_from_directory(key, path)

    def get_stream_url(self, path: Path) -> str:
        key = self.get_library_id_by_path(path)
        return self.get_direct_url(f"/library/metadata/{key}/media/0/file.mkv")


class Nginx:
    """Wrapper around nginx command"""

    _cmd = "nginx"

    @classmethod
    @functools.cache
    def exit_if_not_installed(cls):
        if shutil.which(cls._cmd) is None:
            raise Exit(f"'{cls._cmd}' is not found in PATH", code=2)

    @classmethod
    def _run(cls, *args: tp.Any, what_happens: str, exit_on_error: bool = True) -> str:
        cls.exit_if_not_installed()
        cmd = ["sudo", "-S", cls._cmd, *map(str, args)]
        password = utils.get_sudo_pass(cmd, what_happens=what_happens)
        return utils.run_process(
            cmd,
            exit_on_error=exit_on_error,
            input_=password,
        ).stdout

    @classmethod
    def test(cls):
        echo.info("Testing nginx configuration")
        return cls._run("-t", what_happens="Nginx configuration would be tested")

    @classmethod
    def reload(cls):
        echo.info("Reloading nginx configuration")
        return cls._run(
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
    title: str = ""
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
    comment: str | None = None

    def __repr__(self) -> str:
        return f"{self.title} ({self.filename})"

    @property
    def video(self) -> FfmpegStream:
        video_ = next((s for s in self.streams if s.type == "video"), None)
        if video_ is None:
            raise Exit("Video stream not found")
        return video_

    @property
    def audios(self) -> list[FfmpegStream]:
        return [s for s in self.streams if s.type == "audio"]

    @property
    def subtitles(self) -> list[FfmpegStream]:
        return [s for s in self.streams if s.type == "subtitle"]

    def get_burned_subtitles_lang(self) -> str | None:
        if self.comment:
            match = re.search(r"burned-subs-lang:(\w{2,3})", self.comment)
            if match:
                return match.group(1)
        return None

    @classmethod
    def parse(cls, output: str, filename: Path) -> "FfmpegMediaInfo":
        lines = output.splitlines()

        default_lang: str | None = None
        if len(filename.suffixes) > 1:
            lang = filename.suffixes[-2].lstrip(".")
            if len(lang) in (2, 3):
                default_lang = lang

        default_title = filename.stem.replace("_", " ")

        title: str = ""
        bitrate: str = ""
        comment: str | None = None
        duration: dt.timedelta = dt.timedelta()
        last_stream_info: FfmpegStream | None = None
        streams: list[FfmpegStream] = []

        for i, line in enumerate(lines):
            line = line.strip()
            if "Estimating duration from bitrate" in line:
                continue
            if "from" in line:
                match = re.search(r"from '(.+)'", line)
                if match:
                    filename = Path(match.group(1))
                else:
                    echo.warning(
                        f"{filename} | Cannot parse filename from line: {line}"
                    )
            if "Duration" in line:
                match = re.search(r"Duration: (.+?),", line)
                if match:
                    if match.group(1) != "N/A":
                        duration = utils.parse_duration(match.group(1))
                else:
                    echo.warning(
                        f"{filename} | Cannot parse duration from line: {line}"
                    )
            if "comment" in line:
                match = re.search(r"comment\s+:\s+(.+)", line)
                if match:
                    comment = match.group(1)
                else:
                    echo.warning(f"{filename} | Cannot parse comment from line: {line}")
            if line.startswith("title") and last_stream_info is None:
                match = re.search(r"title\s+:\s+(.+)", line)
                if match:
                    title = match.group(1)
                else:
                    echo.warning(f"{filename} | Cannot parse title from line: {line}")
            if "bitrate" in line:
                match = re.search(r"bitrate:\s+(.+)", line)
                if match:
                    if match.group(1) != "N/A":
                        bitrate = match.group(1)
                else:
                    echo.warning(f"{filename} | Cannot parse bitrate from line: {line}")
            if "Stream" in line:
                if last_stream_info:
                    last_stream_info.title = last_stream_info.title or default_title
                    streams.append(last_stream_info)
                match = re.search(
                    r"Stream #\d+:(\d+)(?:\((\w+)\))?: (\w+): (\w+)(.*)",
                    line,
                )
                if match:
                    index, lang, type_, codec, encoding_info = match.groups()
                    last_stream_info = FfmpegStream(
                        index=int(index),
                        type=type_.lower(),  # type: ignore
                        codec=codec.lower().strip(),
                        language=lang or default_lang,
                        encoding_info=encoding_info.split(",", 1)[-1].strip(),
                    )
                else:
                    echo.warning(
                        f"{filename} | Cannot parse stream info from line: {line}"
                    )
            if line.startswith("title") and last_stream_info:
                match = re.search(r"title\s+:\s+(.+)", line)
                if match:
                    last_stream_info.title = match.group(1)
                else:
                    echo.warning(
                        f"{filename} | Cannot parse stream title from line: {line}"
                    )

        if last_stream_info:
            last_stream_info.title = last_stream_info.title or default_title
            streams.append(last_stream_info)

        return cls(
            filename=utils.resolve_path_pwd(filename),
            title=title or default_title,
            bitrate=bitrate,
            duration=duration,
            streams=streams,
            comment=comment,
        )

    def to_dict(self) -> dict[str, tp.Any]:
        d = dataclasses.asdict(self)
        d["duration"] = str(d["duration"])
        d["filename"] = d["filename"].as_posix()
        return d


class Ffmpeg:
    """Wrapper around ffmpeg command"""

    _cmd = "ffmpeg"

    @classmethod
    @functools.cache
    def exit_if_not_installed(cls):
        if shutil.which(cls._cmd) is None:
            raise Exit(f"'{cls._cmd}' is not found in PATH", code=2)

    @classmethod
    def _run(cls, *args: tp.Any, **kwargs) -> str:
        cls.exit_if_not_installed()
        cmd = [cls._cmd, *map(str, args)]
        return utils.run_process(cmd, **kwargs).stdout

    @classmethod
    @functools.cache
    def get_media_info(cls, path: Path) -> FfmpegMediaInfo:
        path = utils.resolve_path_pwd(path)
        res = cls._run(
            "-i",
            path,
            "-hide_banner",
            exit_on_error=False,
        )
        return FfmpegMediaInfo.parse(res, path.relative_to(path.parent))

    @classmethod
    def print_media_info(cls, path: Path) -> FfmpegMediaInfo:
        media_file_info = cls.get_media_info(path)
        echo.info("Media info:")
        echo.print(utils.bb("Filename: ") + media_file_info.filename.as_posix())
        if media_file_info.title:
            echo.print(utils.bb("Title: ") + media_file_info.title)
        if media_file_info.bitrate:
            echo.print(utils.bb("Bitrate: ") + media_file_info.bitrate)
        if media_file_info.duration:
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
        subtitle_lang = (
            subtitle.language or subtitle_lang or utils.prompt_subtitles(subtitle)
        ).lower()[:3]
        echo.info(
            f"Extracting subtitle: {subtitle.title} [{subtitle_lang}] from {media_file}"
        )
        subtitle_file = media_file.with_suffix(f".{subtitle_lang[:2]}.{subtitle.codec}")
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
            "-metadata:s:s:0",
            f"language={subtitle_lang}",
            "-y",
            subtitle_file,
            live_output=True,
        )
        return subtitle_file

    def _assert_input_output_equal(self, input_file: Path, output_file: Path):
        if input_file == output_file:
            raise Exit(
                f"ffmpeg: Output file {input_file.name} cannot be the same as input file"
            )

    def convert_to_mp4(
        self,
        media_file: Path,
        output_file: Path,
        audio_lang: str,
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
        self._assert_input_output_equal(media_file, output_file)
        cur_index = 0
        index_audio = index_subtitle = 1
        args = [
            "-i",
            media_file,
        ]
        if audio_file is not None:
            index_audio = cur_index + 1
            cur_index += 1
            args.extend(["-i", audio_file])
        if subtitle_file is not None:
            if not burn_subtitles:
                index_subtitle = cur_index + 1
                cur_index += 1
                args.extend(["-i", subtitle_file])
            subtitle_lang = (
                subtitle_lang or utils.prompt_subtitles(subtitle_file)
            ).lower()[:3]
        args.extend(
            [
                "-map",
                "0:v:0",
            ]
        )
        if burn_subtitles and subtitle_file:
            args.extend(
                [
                    "-c:v",
                    "libx264",
                    "-crf",
                    config.FFPEG_ENCODE_CRF,
                    "-preset",
                    config.FFPEG_ENCODE_PRESET,
                    "-vf",
                    f"subtitles={subtitle_file}",
                    "-metadata",
                    f"comment=burned-subs-lang:{subtitle_lang}",
                ]
            )
        else:  # then copy video stream
            args.extend(
                [
                    "-c:v",
                    "copy",
                ]
            )
        if audio_file:
            args.extend(["-map", f"{index_audio}:a:0", "-c:a", "copy"])
        elif audio_stream:
            args.extend(["-map", f"0:{audio_stream}", "-c:a", "copy"])
        if audio_lang:
            args.extend(["-metadata:s:a:0", f"language={audio_lang.lower()[:3]}"])
        if subtitle_file and not burn_subtitles:
            args.extend(
                [
                    "-map",
                    f"{index_subtitle}:0",
                    "-c:s",
                    "mov_text",
                    "-metadata:s:s:0",
                    f"language={subtitle_lang}",
                ]
            )
        args.extend(["-y", output_file])
        self._run(*args, live_output=True)
        return output_file

    def convert_subtitle_to_vtt(
        self, subtitle_file: Path, subtitle_lang: str | None
    ) -> Path:
        echo.info(f"Converting subtitle file: {subtitle_file} to VTT format")
        output_file = subtitle_file.with_suffix(".vtt")
        self._assert_input_output_equal(subtitle_file, output_file)
        media_info = self.get_media_info(subtitle_file)
        subtitle_lang = (
            subtitle_lang
            or media_info.subtitles[0].language
            or utils.prompt_subtitles(media_info.subtitles[0])
        ).lower()[:3]
        self._run(
            "-i",
            subtitle_file,
            "-c:s",
            "webvtt",
            "-metadata:s:s:0",
            f"language={subtitle_lang}",
            "-y",
            output_file,
            live_output=True,
        )
        return output_file

    def extract_audio_with_convert(
        self,
        media_file: Path,
        stream_index: int,
        output_file: Path,
        audio_lang: str | None = None,
        codec: str | None = None,
        bitrate: str | None = None,
    ) -> Path:
        media_file_info = self.get_media_info(media_file)
        audio = next(
            (s for s in media_file_info.audios if s.index == stream_index), None
        )
        if audio is None:
            audio_streams = [s.index for s in media_file_info.audios]
            raise Exit(
                f"Stream not found: {stream_index}. Available audio streams: {audio_streams}"
            )
        if audio_lang and audio.language and audio.language[:2] != audio_lang[:2]:
            echo.warning(f"Audio language mismatch: {audio.language} != {audio_lang}")
        is_copy = codec is None and bitrate is None
        audio_lang = (
            audio_lang or audio.language or utils.prompt_audio(audio)
        ).lower()[:3]
        audio_codec = codec or audio.codec
        echo.info(
            f"Extracting audio: {audio.title} [{audio_lang}] from {media_file} {'(copy)' if is_copy else f'convert to {audio_codec}'}"
        )
        audio_file = output_file
        if audio_file.exists():
            if utils.confirm(
                f"Audio file already exists: {audio_file.name}. Do you want to overwrite it?"
            ):
                audio_file.unlink()
            else:
                return audio_file
        cmd = [
            "-i",
            media_file,
            "-map",
            f"0:{stream_index}",
            "-metadata:s:a:0",
            f"language={audio_lang}",
        ]
        if is_copy:
            cmd.extend(["-c:a", "copy"])
        else:
            cmd.extend(["-c:a", audio_codec])
            if bitrate:
                cmd.extend(["-b:a", bitrate])
        cmd.extend(["-y", audio_file])
        self._run(*cmd, live_output=True)
        return audio_file

    def convert_audio(
        self,
        audio_file: Path,
        output_file: Path,
        audio_lang: str | None = None,
        codec: str = config.BROWSER_AUDIO_CODEC,
        bitrate: str = config.BROWSER_AUDIO_BITRATE,
    ) -> Path:
        echo.info(f"Converting audio file: {audio_file} to {codec.upper()} format")
        media_info = self.get_media_info(audio_file)
        audio_lang = (
            audio_lang
            or media_info.audios[0].language
            or utils.prompt_audio(media_info.audios[0])
        ).lower()[:3]
        self._assert_input_output_equal(audio_file, output_file)
        self._run(
            "-i",
            audio_file,
            "-c:a",
            codec,
            "-b:a",
            bitrate,
            "-metadata:s:a:0",
            f"language={audio_lang}",
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
        return path.suffixes[-1].lstrip(".")

    @classmethod
    def get_files_with_extensions(
        cls,
        directory: Path,
        extensions: tp.Container[str],
        recursive_depth: int = 2,
        max_dirs: int = config.FS_MAX_DIRS,
    ) -> tp.Generator[Path, None, None]:
        directories: list[Path] = []
        files: list[Path] = []
        for path in directory.iterdir():
            if path.is_dir() and max_dirs > 0 and recursive_depth > 0:
                max_dirs -= 1
                directories.append(path)
            elif path.is_file() and cls.get_extension(path) in extensions:
                files.append(path)
        yield from files
        for path in directories:
            yield from cls.get_files_with_extensions(
                path, extensions, recursive_depth - 1, max_dirs
            )

    @classmethod
    def get_video_files(
        cls,
        directory: Path,
        recursive_depth: int = 2,
    ) -> tp.Generator[Path, None, None]:
        return cls.get_files_with_extensions(
            directory, config.VIDEO_EXTENSIONS, recursive_depth
        )

    @classmethod
    def get_audio_files(
        cls,
        directory: Path,
        recursive_depth: int = 2,
    ) -> tp.Generator[Path, None, None]:
        return cls.get_files_with_extensions(
            directory, config.AUDIO_EXTENSIONS, recursive_depth
        )

    @classmethod
    def get_subtitle_files(
        cls,
        directory: Path,
        recursive_depth: int = 2,
    ) -> tp.Generator[Path, None, None]:
        return cls.get_files_with_extensions(
            directory, config.SUBTITLE_EXTENSIONS, recursive_depth
        )

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
    def create_symlink(symlink_path: Path, target_path: Path, sudo: bool = False):
        if symlink_path.exists() and not symlink_path.is_symlink():
            raise Exit(f"Symlink path already exists as a regular file: {symlink_path}")
        if not target_path.exists():
            raise Exit(f"Target path does not exist: {target_path}")
        echo.info(f"Creating symlink: {symlink_path} -> {target_path}")
        if sudo:
            command = ["sudo", "-S", "ln", "-sf", target_path.as_posix(), symlink_path.as_posix()]
            password = utils.get_sudo_pass(
                command, what_happens="Symlink would be created"
            )
            utils.run_process(command, input_=password)
        else:
            symlink_path.symlink_to(target_path)

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
    def read_file(path: Path, **kwargs) -> str:
        with path.open(**kwargs) as f:
            return f.read()

    @classmethod
    def enforce_utf8(cls, filename: Path) -> Path:
        """Check encoding of file and convert it to UTF-8 if needed"""
        encoding = utils.detect_encoding(filename)
        if encoding == "utf-8":
            return filename
        output_file = filename.with_suffix(f".utf8{filename.suffix}")
        echo.info(
            f"Converting file {filename.name} encoding to UTF-8: {output_file.name}"
        )
        content = cls.read_file(filename, encoding=encoding)
        with output_file.open("w", encoding="utf-8") as f:
            f.write(content)
        return output_file
