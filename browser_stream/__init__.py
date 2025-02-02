#!/usr/local/bin/python
import httpx
import json
import os
from pathlib import Path
import shutil
import typing as tp
import subprocess
import functools
import urllib.parse

import typer

import browser_stream.utils as utils
from browser_stream.echo import echo


class Exit(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        self.message = message
        self.code = code


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

    def _run(self, *args: tp.Any, exit_on_error: bool = True) -> str:
        self.exit_if_not_installed()
        cmd = [self._cmd, *map(str, args)]
        return utils.run_process(cmd, exit_on_error=exit_on_error).stdout

    def test(self):
        echo.info("Testing nginx configuration")
        return self._run("-t")

    def reload(self):
        echo.info("Reloading nginx configuration")
        return self._run("-s", "reload")

    def get_mp4_stream_config(
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
        ssl_config = (
            utils.dedent(f"""\n
                ssl_certificate /etc/letsencrypt/live/{server_name}/fullchain.pem;
                ssl_certificate_key /etc/letsencrypt/live/{server_name}/privkey.pem;
                ssl_protocols TLSv1.2 TLSv1.3;
                ssl_ciphers HIGH:!aNULL:!MD5;

                # Redirect HTTP to HTTPS
                if ($scheme != "https") {{
                    return 301 https://$host$request_uri;
                }}
            """)
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
                server_name {server_name};
                {ssl_config}

                # Block root access
                location = / {{
                    return 403;
                }}

                # Serve media files
                location /media/ {{
                    alias "{media_path.as_posix()}";
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

    def get_streams(self, url: str):
        return self._run(
            "-i", url, "-hide_banner", "-map", "0", "-c", "copy", "-f", "null", "-"
        )

    def extract_subtitle(self, media_file: Path, subtitle_lang: str) -> Path:
        subtitle_file = media_file.with_suffix(f".{subtitle_lang}.vtt")
        self._run(
            "-i",
            media_file,
            "-map",
            f"0:s:{subtitle_lang}",
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
            subtitle_file: Path to subtitle file
            subtitle_lang: Subtitle language
            burn_subtitles: Burn subtitles into video (default: False)
        """
        args = [
            "-i",
            media_file,
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-y",
            output_file,
        ]
        if audio_lang is not None and audio_file is not None:
            args.extend(["-map", "0", "-map", f":a:{audio_lang}", audio_file])
        if subtitle_file is not None:
            if burn_subtitles:
                args.extend(["-vf", f"subtitles={subtitle_file}"])
            elif subtitle_lang is not None:
                args.extend(["-map", f":s:{subtitle_lang}", subtitle_file])
            else:
                args.extend(["-map", "0", "-c:s", "copy", subtitle_file])
        self._run(*args, live_output=True)
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
            <video controls>
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
    def create_dir(path: Path):
        if path.exists():
            return
        echo.info(f"Creating directory: {path}")
        path.mkdir(parents=True)

    @staticmethod
    def write_file(path: Path, content: str):
        echo.info(f"Creating file: {path}")
        with path.open("w") as f:
            f.write(content + "\n")

    @staticmethod
    def create_symlink(src: Path, dst: Path):
        if src.exists() and not src.is_symlink():
            raise Exit(f"Sorce path is not a symlink: {src}")
        echo.info(f"Creating symlink: {src} -> {dst}")
        src.symlink_to(dst)

    @staticmethod
    def remove_symlink(path: Path):
        if not path.exists():
            return
        if not path.is_symlink():
            raise Exit(f"Path is not a symlink: {path}")
        echo.info(f"Removing symlink: {path}")
        path.unlink()

    @staticmethod
    def remove_file(path: Path):
        if not path.exists():
            return
        echo.info(f"Removing file: {path}")
        path.unlink()

    @staticmethod
    def read_file(path: Path) -> str:
        with path.open() as f:
            return f.read()
