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

from browser_stream import (
    stream_nginx,
    stream_plex,
    PlexAPI,
    Exit,
    Ffmpeg,
    FS,
    Nginx,
    HTML,
    conf,
)

import browser_stream.utils as utils
import browser_stream.config as config
from browser_stream.echo import echo, setup_logger


app = typer.Typer(
    name="browser-streamer",
    help=f"""A CLI tool to prepare and manage media for streaming over HTTP using Nginx or Plex direct links.
    
    \b
    [dim]Ver: {config.__version__}[/dim]
    """,
    context_settings=config.CONTEXT_SETTINGS,
    pretty_exceptions_enable=config.PRETTY_EXCEPTIONS,
    rich_markup_mode="rich",
    no_args_is_help=True,
)
media_app = typer.Typer(
    name="media",
    help="Media helpers",
    context_settings=config.CONTEXT_SETTINGS,
    pretty_exceptions_enable=config.PRETTY_EXCEPTIONS,
    rich_markup_mode="rich",
)
app.add_typer(media_app)


@app.command("config")
def config_command():
    """Show configuration"""
    echo.info(f"Config path: {config.CONFIG_PATH}")
    echo.print_json(conf.to_dict())


@app.command("nginx")
def nginx_command(
    media_dir: Path | None = typer.Option(
        conf.media_dir,
        help="Path to media directory",
        dir_okay=True,
        file_okay=False,
        exists=True,
    ),
    ipv6: bool = typer.Option(
        conf.ipv6, help="Enable IPv6 support in Nginx configuration"
    ),
    ipv4: bool = typer.Option(
        conf.ipv4, help="Enable IPv4 support in Nginx configuration"
    ),
    port: int = typer.Option(conf.nginx_port or 32000, help="Port to listen on"),
    ssl: bool = typer.Option(
        True,
        help="Enable SSL support in Nginx configuration, default is True",
        show_default=False,
    ),
    domain_name: str | None = typer.Option(
        conf.nginx_domain_name,
        help="Domain name for SSL certificate",
        show_default=False,
    ),
    update_token: bool = typer.Option(
        False, help="Update X-Token in Nginx configuration"
    ),
    site_conf_name: str = typer.Option(
        conf.nginx_conf_name or "browser_stream",
        help="Name of the Nginx site configuration file",
    ),
    allow_index: bool = typer.Option(
        conf.nginx_allow_index, help="Allow directory listing in Nginx configuration"
    ),
    reset: bool = typer.Option(
        False, help="Remove Nginx configuration files and symlinks related to site"
    ),
):
    """Nginx configuration"""
    media_dir = (
        media_dir or utils.prompt_path("Enter path to media directory")
    ).resolve()

    fs = FS()
    nginx = Nginx()
    nginx.exit_if_not_installed()

    if conf.nginx_conf_name != site_conf_name:
        conf.nginx_conf_name = site_conf_name
        conf.save()

    site_available = Path("/etc/nginx/sites-available") / site_conf_name
    site_enabled = Path("/etc/nginx/sites-enabled") / site_conf_name

    if reset:
        fs.remove_file(site_available, sudo=True)
        fs.remove_file(site_enabled, sudo=True)
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

    nginx_conf_data_new = nginx.get_browser_stream_config(
        media_path=media_dir,
        port=port,
        ipv6=ipv6,
        ipv4=ipv4,
        secret=x_token,
        ssl=ssl,
        allow_index=allow_index,
        server_name=domain_name,
    )
    if (
        site_available.exists()
        and fs.read_file(site_available).strip() == nginx_conf_data_new
    ):
        echo.info("Nginx configuration is up-to-date")
        return
    echo.info("Generating Nginx configuration")
    fs.write_file(site_available, nginx_conf_data_new, sudo=True)
    conf.media_dir = media_dir
    conf.nginx_port = port
    conf.ipv4 = ipv4
    conf.ipv6 = ipv6
    conf.nginx_secret = x_token
    conf.nginx_allow_index = allow_index
    conf.nginx_domain_name = domain_name
    conf.save()
    nginx.test()
    nginx.reload()

    if not site_enabled.exists():
        fs.create_symlink(site_enabled, site_available, sudo=True)
    echo.info("Nginx configuration generated successfully")


@media_app.command("info")
def media_info_command(
    media_file: Path = typer.Option(
        ...,
        help="Path to media file",
        dir_okay=False,
        file_okay=True,
        exists=True,
        prompt=True,
        show_default=False,
    ),
):
    ffmeg = Ffmpeg()
    echo.info("Media info:")
    echo.print_json(ffmeg.get_media_info(media_file).to_dict())


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


@app.command("stream")
def stream_command(
    media_file: Path | None = typer.Option(
        None,
        help="Path to media file",
        dir_okay=False,
        file_okay=True,
        exists=True,
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
    burn_subtitles: bool = typer.Option(
        False,
        help="Burn subtitles into video stream",
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
    elif not with_nginx and not with_plex:
        raise typer.BadParameter(
            "At least one of --with-nginx or --with-plex must be enabled",
            param_hint="--with-nginx or --with-plex",
        )
    media_file = (media_file or utils.prompt_path("Enter path to media file")).resolve()
    if with_nginx:
        stream_nginx(
            media_file=media_file,
            subtitle_file=subtitle_file,
            subtitle_lang=subtitle_lang,
            burn_subtitles=burn_subtitles,
            audio_lang=audio_lang,
            audio_file=audio_file,
            do_not_convert=do_not_convert,
        )
    elif with_plex:
        stream_plex(
            media_file=media_file,
            subtitle_file=subtitle_file,
            subtitle_lang=subtitle_lang,
            burn_subtitles=burn_subtitles,
            audio_lang=audio_lang,
            do_not_convert=do_not_convert,
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
