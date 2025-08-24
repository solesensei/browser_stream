import typing as tp
import sys
from pathlib import Path
import typer
import click

from browser_stream import (
    stream_nginx,
    stream_plex,
    PlexAPI,
    Exit,
    exit_if,
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
setup_app = typer.Typer(
    name="setup",
    help="Setup server (Nginx / Plex)",
    context_settings=config.CONTEXT_SETTINGS,
    pretty_exceptions_enable=config.PRETTY_EXCEPTIONS,
    rich_markup_mode="rich",
)
media_app = typer.Typer(
    name="media",
    help="Media helpers",
    context_settings=config.CONTEXT_SETTINGS,
    pretty_exceptions_enable=config.PRETTY_EXCEPTIONS,
    rich_markup_mode="rich",
)
app.add_typer(setup_app)
app.add_typer(media_app)


@app.command("config")
def config_command(reset: bool = typer.Option(False, help="Reset configuration")):
    """Show and reset configuration"""
    if reset:
        fs = FS()
        fs.remove_file(Path(config.CONFIG_PATH))
        echo.info("Configuration reset complete")
        return
    echo.info(f"Config path: {config.CONFIG_PATH}")
    echo.print_json(conf.to_dict())


@setup_app.command("nginx")
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
    media_dir = utils.resolve_path_pwd(
        media_dir or utils.prompt_path("Enter path to media directory")
    )
    echo.info(f"Media directory: {media_dir}")

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
        res = utils.select_options_interactive(
            option_name="Which protocols to enable in Nginx configuration?",
            options=["IPv6", "IPv4", "Both"],
        )
        ipv6 = "IPv6" in res or "Both" in res
        ipv4 = "IPv4" in res or "Both" in res

    if ssl and not domain_name:
        echo.info("SSL `--ssl` is enabled, domain name is required")
        domain_name = utils.prompt("Enter domain name for SSL certificate")

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
        fs.create_symlink(symlink_path=site_enabled, target_path=site_available, sudo=True)
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


@setup_app.command("plex")
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
    media: Path = typer.Argument(
        help="Path to media file or directory",
        exists=True,
        file_okay=True,
        dir_okay=True,
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
    embed_subs: bool = typer.Option(
        False,
        help="Embed subtitles into MP4 file",
        show_default=False,
    ),
    raw: bool = typer.Option(
        False,
        help="Skip converting media file to MP4 format (quick streaming)",
        show_default=False,
    ),
    server: str = typer.Option(
        "nginx",
        help="Streaming server to use (nginx or plex)",
        show_default=True,
        click_type=click.Choice(["nginx", "plex"], case_sensitive=False),
    ),
    scan_external: bool = typer.Option(
        False,
        help="Scan directory for external audio/subtitle files when single file provided",
        show_default=False,
    ),
    prepare_only: bool = typer.Option(
        False,
        help="Only prepare/convert media files, don't generate streaming URLs",
        show_default=False,
    ),
):
    """Stream media file using Nginx or Plex

    \b
    Examples:

        Stream media file using Nginx server (default):
        $ browser-streamer stream /path/to/media.mp4

        Stream media file using Plex:
        $ browser-streamer stream /path/to/media.mp4 --server=plex
        
        Quick streaming without conversion:
        $ browser-streamer stream /path/to/media.mp4 --raw
        
        Stream directory (scan for video files):
        $ browser-streamer stream /path/to/media/directory/
        
        Scan for external audio/subtitle files for single movie:
        $ browser-streamer stream movie.mkv --scan-external
        
        Prepare media for streaming without generating URLs:
        $ browser-streamer stream movie.mkv --prepare-only
    """
    with_nginx = server.lower() == "nginx"
    with_plex = server.lower() == "plex"
    
    # Determine scanning behavior:
    # - Always scan if media is a directory
    # - For single files: scan only if --scan-external is used
    # - Don't scan if specific files provided or --raw mode
    is_directory = media.is_dir()
    has_specific_files = (audio_file is not None or subtitle_file is not None)
    should_scan = (is_directory or scan_external) and not has_specific_files and not raw
    
    media = utils.resolve_path_pwd(media)
    
    if prepare_only:
        if raw:
            # With --raw, no conversion needed, just inform user
            echo.info(f"Media file ready for raw streaming: {media}")
        else:
            # Only prepare/convert media, don't generate streaming URLs
            from browser_stream import prepare_file_to_stream
            stream_media = prepare_file_to_stream(
                media=media,
                audio_file=audio_file,
                audio_lang=audio_lang,
                subtitle_file=subtitle_file,
                subtitle_lang=subtitle_lang,
                burn_subtitles=burn_subtitles,
                add_subtitles_to_mp4=embed_subs,
                no_scan=not should_scan,
            )
            echo.info(f"Media prepared: {stream_media.path}")
            if stream_media.subtitle_path:
                echo.info(f"Subtitles prepared: {stream_media.subtitle_path}")
    elif with_nginx:
        stream_nginx(
            media=media,
            subtitle_file=subtitle_file,
            subtitle_lang=subtitle_lang,
            burn_subtitles=burn_subtitles,
            audio_lang=audio_lang,
            audio_file=audio_file,
            do_not_convert=raw,
            add_subtitles_to_mp4=embed_subs,
            no_scan=not should_scan,
        )
    elif with_plex:
        stream_plex(
            media=media,
            subtitle_file=subtitle_file,
            subtitle_lang=subtitle_lang,
            burn_subtitles=burn_subtitles,
            audio_lang=audio_lang,
            do_not_convert=raw,
            no_scan=not should_scan,
        )
    echo.info("Completed")


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
