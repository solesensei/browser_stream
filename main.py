#!/usr/local/bin/python
import httpx
import json
import os
import shutil
import typing as tp
import subprocess
import functools
import sys
import urllib.parse
from pathlib import Path
import typer

from browser_stream import PlexAPI, Exit, Ffmpeg, FS, Nginx, HTML

import browser_stream.utils as utils
import browser_stream.config as config
from browser_stream.echo import echo, setup_logger


app = typer.Typer(
    name="browser-streamer",
    help=f"A CLI tool to prepare and manage media for streaming over HTTP using Nginx or Plex direct link.\n\nVer: {config.VERSION}",
    context_settings=config.CONTEXT_SETTINGS,
    pretty_exceptions_enable=config.PRETTY_EXCEPTIONS,
    rich_markup_mode="rich",
    no_args_is_help=True,
)

conf = utils.Config.load()


@app.command("nginx")
def nginx_command(
    media_dir: Path = typer.Option(
        ...,
        help="Path to media directory",
        dir_okay=True,
        file_okay=False,
        exists=True,
        prompt=True,
    ),
    ipv6: bool = typer.Option(False, help="Enable IPv6 support in Nginx configuration"),
    ipv4: bool = typer.Option(False, help="Enable IPv4 support in Nginx configuration"),
    port: int = typer.Option(32000, help="Port to listen on"),
    update_token: bool = typer.Option(
        False, help="Update X-Token in Nginx configuration"
    ),
    site_conf_name: str = typer.Option(
        conf.nginx_conf_name or "mp4_stream",
        help="Name of the Nginx site configuration file",
    ),
    reset: bool = typer.Option(
        False, help="Remove Nginx configuration files and symlinks related to site"
    ),
):
    """Nginx configuration"""
    fs = FS()
    nginx = Nginx()
    nginx.exit_if_not_installed()

    if conf.nginx_conf_name != site_conf_name:
        conf.nginx_conf_name = site_conf_name
        conf.save()

    site_available = Path("/etc/nginx/sites-available") / site_conf_name
    site_enabled = Path("/etc/nginx/sites-enabled") / site_conf_name

    if reset:
        fs.remove_file(site_available)
        fs.remove_file(site_enabled)
        echo.info("Nginx configuration reset complete")
        return

    if not ipv6 and not ipv4:
        raise typer.BadParameter(
            "At least one of --ipv6 or --ipv4 must be enabled",
            param_hint="--ipv6 or --ipv4",
        )

    if update_token or not conf.nginx_secret:
        echo.info("Generating new X-Token")
        x_token = utils.generate_token()
    else:
        x_token = conf.nginx_secret

    nginx_conf_data_new = nginx.get_mp4_stream_config(
        media_path=media_dir,
        port=port,
        ipv6=ipv6,
        ipv4=ipv4,
        secret=x_token,
    )
    if site_available.exists() and fs.read_file(site_available) == nginx_conf_data_new:
        echo.info("Nginx configuration is up-to-date")
        return
    fs.write_file(site_available, nginx_conf_data_new)
    conf.media_dir = media_dir
    conf.nginx_port = port
    conf.ipv4 = ipv4
    conf.ipv6 = ipv6
    conf.nginx_secret = x_token
    conf.save()
    nginx.test()
    nginx.reload()

    if not site_enabled.exists():
        fs.create_symlink(site_enabled, site_available)
    echo.info("Nginx configuration generated successfully")


@app.command("plex")
def plex_command(
    x_token: tp.Annotated[str, typer.Option(help="X-Plex-Token")],
    base_url: tp.Annotated[
        str, typer.Option(help="Base URL of Plex server")
    ] = "http://localhost:32400",
    path: tp.Annotated[
        Path | None, typer.Option(help="Path to media file", file_okay=True)
    ] = None,
):
    """Plex configuration"""
    path = path or utils.prompt_path("Enter path to media file")
    plex = PlexAPI(x_token, base_url)
    echo.print_json(plex.get_library_id_by_path(path))


def build_stream_url_ngix(
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


def prepare_file_to_stream(
    media_file: Path,
    audio_file: Path | None = None,
    audio_lang: str | None = None,
    subtitle_file: Path | None = None,
    subtitle_lang: str | None = None,
) -> Path:
    fs = FS()
    ffmpeg = Ffmpeg()
    if fs.get_extension(media_file) != ".mp4":
        output_file = media_file.with_suffix(".mp4")
        if output_file.exists() and not utils.confirm(
            f"File already exists: {output_file}, do you want to overwrite it?"
        ):
            echo.warning("Skipping conversion. File already exists")
            media_file = output_file
        else:
            media_file = ffmpeg.convert_to_mp4(
                media_file,
                output_file,
                audio_file=audio_file,
                audio_lang=audio_lang,
                subtitle_file=subtitle_file,
                subtitle_lang=subtitle_lang,
            )
    if subtitle_file is None and subtitle_lang:
        subtitle_file = ffmpeg.extract_subtitle(media_file, subtitle_lang)

    if subtitle_file:
        html = HTML()
        html.get_video_html_with_subtitles()


def stream_nginx(
    media_file: Path,
    audio_file: Path | None = None,
    audio_lang: str | None = None,
    subtitle_file: Path | None = None,
    subtitle_lang: str | None = None,
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

    if not do_not_convert:
        media_file = prepare_file_to_stream(
            media_file=media_file,
            audio_file=audio_file,
            audio_lang=audio_lang,
            subtitle_file=subtitle_file,
            subtitle_lang=subtitle_lang,
        )


def stream_plex(
    media_file: Path,
    audio_lang: str | None = None,
    subtitle_file: Path | None = None,
    subtitle_lang: str | None = None,
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


@app.command("stream")
def stream_command(
    media_file: Path = typer.Option(
        ...,
        help="Path to media file",
        dir_okay=False,
        file_okay=True,
        exists=True,
        prompt=True,
        show_default=False,
    ),
    audio_lang: str | None = typer.Option(
        None,
        help="Audio language, e.g. English, Spanish",
        show_default=False,
    ),
    audio_file: Path | None = typer.Option(
        None,
        help="Path to audio file",
        dir_okay=False,
        file_okay=True,
        exists=True,
        show_default=False,
    ),
    subtitle_lang: str | None = typer.Option(
        None,
        help="Subtitle language, e.g. English, Spanish",
        show_default=False,
    ),
    subtitle_file: Path | None = typer.Option(
        None,
        help="Path to subtitle file",
        dir_okay=False,
        file_okay=True,
        exists=True,
        show_default=False,
    ),
    do_not_convert: bool = typer.Option(
        False,
        help="Skip converting media file to MP4 format. Will not work for browsers",
        show_default=False,
    ),
    with_nginx: bool = typer.Option(
        False,
        help="Stream media file using Nginx server",
        show_default=False,
    ),
    with_plex: bool = typer.Option(
        False,
        help="Stream media file using Plex direct.url",
        show_default=False,
    ),
):
    """Stream media file using Nginx or Plex

    \b
    Examples:

        Stream media file using Nginx server:
        $ browser-streamer stream /path/to/media.mp4 --with-nginx

        Stream media file using Plex direct.url:
        $ browser-streamer stream /path/to/media.mp4 --with-plex
    """
    if with_nginx and with_plex:
        raise typer.BadParameter(
            "Only one of --with-nginx or --with-plex can be enabled",
            param_hint="--with-nginx or --with-plex",
        )
    media_file = media_file.resolve()
    if with_nginx:
        stream_nginx(
            media_file=media_file,
            subtitle_file=subtitle_file,
            subtitle_lang=subtitle_lang,
            audio_lang=audio_lang,
            audio_file=audio_file,
            do_not_convert=do_not_convert,
        )
    elif with_plex:
        stream_plex(
            media_file=media_file,
            subtitle_file=subtitle_file,
            subtitle_lang=subtitle_lang,
            audio_lang=audio_lang,
            do_not_convert=do_not_convert,
        )
    else:
        raise typer.BadParameter(
            "At least one of --with-nginx or --with-plex must be enabled",
            param_hint="--with-nginx or --with-plex",
        )


def run():
    try:
        setup_logger()
        app()
    except Exit as e:
        echo.debug(f"Exit: {e.code}")
        if e.code == 0:
            echo.printc(e.message, color="green", bold=True)
        else:
            echo.printc(f"Error: {e.message}", color="red", bold=True)
        if config.RAISE_EXCEPTIONS:
            raise
        sys.exit(e.code)
    except Exception as e:
        if config.RAISE_EXCEPTIONS:
            raise
        echo.printc(f"{e.__class__.__name__}: {e}", color="red", bold=True)
        echo.printc(
            "Set RAISE_EXCEPTIONS=true environment variable to raise exceptions"
        )
        sys.exit(2)


if __name__ == "__main__":
    run()
