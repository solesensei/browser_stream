import shlex
import sys
import typing as tp
from enum import Enum
from pathlib import Path

import click
import typer
from rich.console import Console
from rich.table import Table

import browser_stream.config as config
import browser_stream.utils as utils
from browser_stream import (
    FS,
    HTML,
    Exit,
    Ffmpeg,
    MediaResult,
    Nginx,
    PlexAPI,
    batch_prepare_episodes,
    conf,
    prepare_file_to_stream,
    resolve_stream,
    setup_batch_processing,
    stream_nginx,
    stream_plex,
)
from browser_stream.echo import echo, setup_logger
from browser_stream.utils import PromptNeeded

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


class MediaStreamType(str, Enum):
    """Media stream types for filtering."""

    audio = "audio"
    subtitle = "subtitle"
    video = "video"


@app.callback()
def app_callback(
    yes: bool = typer.Option(False, "--yes", help="Non-interactive mode (no prompts)"),
    json: bool = typer.Option(
        False, "--json", help="JSON output mode (implies --non-interactive)"
    ),
    log_level: str | None = typer.Option(
        None,
        "--log-level",
        help="Log level (debug|info|warn|error)",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Overwrite existing files",
    ),
):
    """Global options for all commands."""
    if json:
        config.JSON_OUTPUT = True
        config.NON_INTERACTIVE = True
    elif yes:
        config.NON_INTERACTIVE = True

    if log_level is not None:
        config.LOG_LEVEL = log_level

    if overwrite:
        config.OVERWRITE_DEFAULT = overwrite

    setup_logger(log_level=config.LOG_LEVEL)


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
        fs.create_symlink(
            symlink_path=site_enabled, target_path=site_available, sudo=True
        )
    echo.info("Nginx configuration generated successfully")


@media_app.command("info")
def media_info_command(
    media_file: Path = typer.Argument(
        ...,
        help="Path to media file",
        exists=True,
    ),
    only: MediaStreamType | None = typer.Option(
        None,
        help="Filter to only audio, subtitle, or video streams",
    ),
):
    """Get media information (streams, codecs, duration)."""
    ffmpeg = Ffmpeg()
    info = ffmpeg.get_media_info(media_file)

    if config.JSON_OUTPUT:
        result = info.to_dict()
        if only:
            filtered_streams = [
                s for s in result.get("streams", []) if s.get("type") == only.value
            ]
            result = {only.value: filtered_streams}
        echo.print_json(result)
    else:
        console = Console()

        # Video stream
        if not only or only == MediaStreamType.video:
            try:
                video = info.video
                table = Table(title="Video Streams")
                table.add_column("Index", justify="right")
                table.add_column("Codec")
                table.add_row(
                    str(video.index),
                    video.codec or "unknown",
                )
                console.print(table)
            except Exit:
                pass

        # Audio streams
        if not only or only == MediaStreamType.audio:
            if info.audios:
                table = Table(title="Audio Streams")
                table.add_column("Index", justify="right")
                table.add_column("Codec")
                table.add_column("Language")
                for stream in info.audios:
                    table.add_row(
                        str(stream.index),
                        stream.codec or "unknown",
                        stream.language or "unknown",
                    )
                console.print(table)

        # Subtitle streams
        if not only or only == MediaStreamType.subtitle:
            if info.subtitles:
                table = Table(title="Subtitle Streams")
                table.add_column("Index", justify="right")
                table.add_column("Codec")
                table.add_column("Language")
                for stream in info.subtitles:
                    table.add_row(
                        str(stream.index),
                        stream.codec or "unknown",
                        stream.language or "unknown",
                    )
                console.print(table)


@media_app.command("extract-audio")
def media_extract_audio_command(
    media_file: Path = typer.Argument(..., help="Path to media file", exists=True),
    stream: int = typer.Option(None, "--stream", help="Audio stream index (0-based)"),
    lang: str = typer.Option(None, "--lang", help="Audio language code (e.g., eng, jpn)"),
    codec: str = typer.Option("aac", "--codec", help="Output audio codec"),
    bitrate: str = typer.Option("192k", "--bitrate", help="Output audio bitrate"),
    output: Path = typer.Option(None, "-o", "--output", help="Output file path"),
):
    """Extract audio stream from media file."""
    ffmpeg = Ffmpeg()
    info = ffmpeg.get_media_info(media_file)

    try:
        audio_stream = resolve_stream(info, "audio", stream=stream, lang=lang)
    except PromptNeeded:
        raise
    except Exit:
        raise

    if output is None:
        output = utils.get_file_path(
            media_file, codec, audio_stream.language or "unknown", suffix="audio"
        )

    if output.exists() and not config.OVERWRITE_DEFAULT:
        result = MediaResult(
            command="media extract-audio",
            input=str(media_file),
            output=str(output),
            error=f"Output file already exists: {output}. Use --overwrite to replace it.",
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
        raise Exit(result.error, code=1)

    output.parent.mkdir(parents=True, exist_ok=True)
    input_size = media_file.stat().st_size

    try:
        ffmpeg.extract_audio_with_convert(
            media_file=media_file,
            stream_index=info.audios.index(audio_stream),
            output_file=output,
            codec=codec,
            bitrate=bitrate,
        )
        output_size = output.stat().st_size
        result = MediaResult(
            command="media extract-audio",
            input=str(media_file),
            output=str(output),
            input_size=input_size,
            output_size=output_size,
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
    except Exception as e:
        result = MediaResult(
            command="media extract-audio",
            input=str(media_file),
            output=str(output),
            error=str(e),
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
        raise Exit(f"Extract audio failed: {e}", code=1) from e


@media_app.command("extract-subs")
def media_extract_subs_command(
    media_file: Path = typer.Argument(..., help="Path to media file", exists=True),
    stream: int = typer.Option(None, "--stream", help="Subtitle stream index (0-based)"),
    lang: str = typer.Option(
        None, "--lang", help="Subtitle language code (e.g., eng, jpn)"
    ),
    format: str = typer.Option("srt", "--format", help="Output format (srt or vtt)"),
    output: Path = typer.Option(None, "-o", "--output", help="Output file path"),
):
    """Extract subtitle stream from media file."""
    ffmpeg = Ffmpeg()
    info = ffmpeg.get_media_info(media_file)

    try:
        sub_stream = resolve_stream(info, "subtitle", stream=stream, lang=lang)
    except PromptNeeded:
        raise
    except Exit:
        raise

    if output is None:
        lang_code = sub_stream.language or "unknown"
        output = utils.get_file_path(media_file, format, lang_code, suffix="subs")

    if output.exists() and not config.OVERWRITE_DEFAULT:
        result = MediaResult(
            command="media extract-subs",
            input=str(media_file),
            output=str(output),
            error=f"Output file already exists: {output}. Use --overwrite to replace it.",
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
        raise Exit(result.error, code=1)

    output.parent.mkdir(parents=True, exist_ok=True)
    input_size = media_file.stat().st_size

    try:
        sub_idx = info.subtitles.index(sub_stream)
        ffmpeg.extract_subtitle(
            media_file=media_file,
            stream_index=sub_idx,
            subtitle_lang=sub_stream.language,
        )
        if format == "vtt":
            vtt_output = output.with_suffix(".vtt")
            ffmpeg.convert_subtitle_to_vtt(output, subtitle_lang=sub_stream.language)
            output.unlink()
            output = vtt_output

        output_size = output.stat().st_size
        result = MediaResult(
            command="media extract-subs",
            input=str(media_file),
            output=str(output),
            input_size=input_size,
            output_size=output_size,
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
    except Exception as e:
        result = MediaResult(
            command="media extract-subs",
            input=str(media_file),
            output=str(output),
            error=str(e),
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
        raise Exit(f"Extract subtitles failed: {e}", code=1) from e


@media_app.command("convert-subs")
def media_convert_subs_command(
    subtitle_file: Path = typer.Argument(..., help="Path to subtitle file", exists=True),
    lang: str = typer.Option(
        None, "--lang", help="Subtitle language code (eng, jpn, etc.)"
    ),
    to: str = typer.Option("vtt", "--to", help="Output format (vtt)"),
    output: Path = typer.Option(None, "-o", "--output", help="Output file path"),
):
    """Convert subtitle file to VTT format."""
    if output is None:
        output = subtitle_file.with_suffix(".vtt")

    if output.exists() and not config.OVERWRITE_DEFAULT:
        result = MediaResult(
            command="media convert-subs",
            input=str(subtitle_file),
            output=str(output),
            error=f"Output file already exists: {output}. Use --overwrite to replace it.",
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
        raise Exit(result.error, code=1)

    output.parent.mkdir(parents=True, exist_ok=True)
    input_size = subtitle_file.stat().st_size

    try:
        fs = FS()
        fs.enforce_utf8(subtitle_file)

        ffmpeg = Ffmpeg()
        # If language not specified, try to detect from subtitle stream
        subtitle_lang = lang
        if not subtitle_lang:
            try:
                sub_info = ffmpeg.get_media_info(subtitle_file)
                if sub_info.subtitles:
                    subtitle_lang = sub_info.subtitles[0].language or "eng"
                else:
                    subtitle_lang = "eng"
            except Exception:
                subtitle_lang = "eng"

        ffmpeg.convert_subtitle_to_vtt(subtitle_file, subtitle_lang=subtitle_lang)
        output_size = output.stat().st_size

        result = MediaResult(
            command="media convert-subs",
            input=str(subtitle_file),
            output=str(output),
            input_size=input_size,
            output_size=output_size,
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
    except Exception as e:
        result = MediaResult(
            command="media convert-subs",
            input=str(subtitle_file),
            output=str(output),
            error=str(e),
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
        raise Exit(f"Convert subtitles failed: {e}", code=1) from e


@media_app.command("embed-subs")
def media_embed_subs_command(
    video_file: Path = typer.Argument(..., help="Path to video file", exists=True),
    subtitle_file: Path = typer.Option(
        ..., "--subtitles", "-s", help="Path to subtitle file", exists=True
    ),
    lang: str = typer.Option(None, "--lang", help="Subtitle language code"),
    burn: bool = typer.Option(False, "--burn", help="Burn subtitles into video"),
    output: Path = typer.Option(None, "-o", "--output", help="Output file path"),
):
    """Embed subtitles into video file."""
    if output is None:
        output = video_file.with_suffix(".mp4")

    if output.exists() and not config.OVERWRITE_DEFAULT:
        result = MediaResult(
            command="media embed-subs",
            input=str(video_file),
            output=str(output),
            error=f"Output file already exists: {output}. Use --overwrite to replace it.",
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
        raise Exit(result.error, code=1)

    output.parent.mkdir(parents=True, exist_ok=True)
    input_size = video_file.stat().st_size

    try:
        ffmpeg = Ffmpeg()
        # If subtitle language not specified, try to detect from subtitle stream
        subtitle_lang = lang
        if not subtitle_lang:
            try:
                sub_info = ffmpeg.get_media_info(subtitle_file)
                if sub_info.subtitles:
                    subtitle_lang = sub_info.subtitles[0].language or "eng"
                else:
                    subtitle_lang = "eng"
            except Exception:
                subtitle_lang = "eng"

        ffmpeg.convert_to_mp4(
            media_file=video_file,
            output_file=output,
            audio_lang="eng",  # Default audio lang for metadata
            subtitle_file=subtitle_file,
            subtitle_lang=subtitle_lang,
            burn_subtitles=burn,
        )
        output_size = output.stat().st_size

        result = MediaResult(
            command="media embed-subs",
            input=str(video_file),
            output=str(output),
            input_size=input_size,
            output_size=output_size,
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
    except Exception as e:
        result = MediaResult(
            command="media embed-subs",
            input=str(video_file),
            output=str(output),
            error=str(e),
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
        raise Exit(f"Embed subtitles failed: {e}", code=1) from e


@media_app.command("html")
def media_html_command(
    video_file: Path = typer.Argument(..., help="Path to video file", exists=True),
    subtitles: Path = typer.Option(..., help="Path to subtitle file"),
    lang: str = typer.Option("eng", "--lang", help="Subtitle language"),
    output: Path = typer.Option(None, "-o", "--output", help="Output file path"),
):
    """Generate HTML video player with subtitles."""
    if output is None:
        output = video_file.with_suffix(".html")

    if output.exists() and not config.OVERWRITE_DEFAULT:
        result = MediaResult(
            command="media html",
            input=str(video_file),
            output=str(output),
            error=f"Output file already exists: {output}. Use --overwrite to replace it.",
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
        raise Exit(result.error, code=1)

    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        html = HTML()
        html_content = html.get_video_html_with_subtitles(video_file, subtitles, lang)
        output.write_text(html_content)

        result = MediaResult(
            command="media html",
            input=str(video_file),
            output=str(output),
            output_size=output.stat().st_size,
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
    except Exception as e:
        result = MediaResult(
            command="media html",
            input=str(video_file),
            output=str(output),
            error=str(e),
        )
        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
        raise Exit(f"Generate HTML failed: {e}", code=1) from e


@media_app.command("repack")
def media_repack_command(
    media: Path = typer.Argument(
        help="Path to media file or directory",
        exists=True,
        file_okay=True,
        dir_okay=True,
    ),
    audio_streams: str = typer.Option(
        None,
        "--audio-streams",
        help="Audio stream indices to include (comma-separated, e.g. 0,2)",
    ),
    audio_lang: str = typer.Option(
        None,
        "--audio-lang",
        help="Audio language code(s) to include (comma-separated, e.g. jpn,eng)",
    ),
    subtitle_streams: str = typer.Option(
        None,
        "--subtitle-streams",
        help="Subtitle stream indices to include (comma-separated, e.g. 0,1)",
    ),
    subtitle_lang: str = typer.Option(
        None,
        "--subtitle-lang",
        help="Subtitle language code(s) to include (comma-separated, e.g. eng,rus)",
    ),
    subtitle_file: Path = typer.Option(
        None,
        "--subtitle-file",
        help="External subtitle file to embed",
        exists=True,
    ),
    re_encode_video: bool = typer.Option(
        False,
        "--re-encode-video",
        help="Re-encode video stream (uses FFPEG_ENCODE_CRF and FFPEG_ENCODE_PRESET)",
    ),
    extra_args: str = typer.Option(
        None,
        "--extra-args",
        help="Additional ffmpeg arguments (as a quoted string)",
    ),
    output: Path = typer.Option(None, "-o", "--output", help="Output file path"),
):
    """Repack media file to MP4 with selected streams."""

    media = utils.resolve_path_pwd(media)

    # If external subtitle file provided, embed it first
    if subtitle_file:
        ffmpeg = Ffmpeg()
        temp_media = media.with_stem(f"{media.stem}.with_subs")
        try:
            # Detect subtitle language
            sub_lang = None
            try:
                sub_info = ffmpeg.get_media_info(subtitle_file)
                if sub_info.subtitles:
                    sub_lang = sub_info.subtitles[0].language or "eng"
                else:
                    sub_lang = "eng"
            except Exception:
                sub_lang = "eng"

            ffmpeg.convert_to_mp4(
                media_file=media,
                output_file=temp_media,
                audio_lang="eng",
                subtitle_file=subtitle_file,
                subtitle_lang=sub_lang,
            )
            media = temp_media  # Use the temp file with embedded subs for repack
        except Exception as e:
            raise Exit(f"Failed to embed subtitle file: {e}", code=1) from e

    # Parse stream indices if provided
    audio_indices: list[int] | None = None
    subtitle_indices: list[int] | None = None
    audio_langs: list[str] | None = None
    subtitle_langs: list[str] | None = None

    if audio_streams and audio_lang:
        raise Exit("Cannot specify both --audio-streams and --audio-lang", code=1)
    if subtitle_streams and subtitle_lang:
        raise Exit("Cannot specify both --subtitle-streams and --subtitle-lang", code=1)

    if audio_streams:
        audio_indices = [int(x.strip()) for x in audio_streams.split(",")]
    elif audio_lang:
        audio_langs = [x.strip() for x in audio_lang.split(",")]

    if subtitle_streams:
        subtitle_indices = [int(x.strip()) for x in subtitle_streams.split(",")]
    elif subtitle_lang:
        subtitle_langs = [x.strip() for x in subtitle_lang.split(",")]

    # Build extra_args list
    extra_args_list: list[str] | None = None
    if re_encode_video or extra_args:
        extra_args_list = []
        if re_encode_video:
            extra_args_list.extend(
                [
                    "-c:v",
                    "libx264",
                    "-crf",
                    config.FFPEG_ENCODE_CRF,
                    "-preset",
                    config.FFPEG_ENCODE_PRESET,
                ]
            )
        if extra_args:
            extra_args_list.extend(shlex.split(extra_args))

    # Handle directory input
    if media.is_dir():
        fs = FS()
        video_files = list(fs.get_video_files(media, recursive_depth=0))
        if not video_files:
            raise Exit(f"No video files found in {media}", code=1)

        if config.JSON_OUTPUT:
            results = []
            has_error = False
            for video_file in video_files:
                result = _repack_single_file(
                    video_file,
                    audio_indices,
                    subtitle_indices,
                    audio_langs,
                    subtitle_langs,
                    extra_args_list,
                    output,
                )
                results.append(result.to_dict())
                if result.error:
                    has_error = True
            echo.print_json({"files": results})
            if has_error:
                raise Exit("One or more files failed to repack", code=1)
        else:
            console = Console()
            table = Table(title="Repack Results")
            table.add_column("Filename")
            table.add_column("Size", justify="right")
            table.add_column("Status")
            table.add_column("Note")

            for video_file in video_files:
                result = _repack_single_file(
                    video_file,
                    audio_indices,
                    subtitle_indices,
                    audio_langs,
                    subtitle_langs,
                    extra_args_list,
                    output,
                )
                if result.error:
                    table.add_row(
                        video_file.name,
                        "",
                        "[red]Failed[/red]",
                        result.error[:80],
                    )
                elif result.skipped:
                    table.add_row(
                        video_file.name,
                        "",
                        "[yellow]Skipped[/yellow]",
                        result.note,
                    )
                else:
                    size_str = f"{utils.format_size(result.input_size or 0)} -> {utils.format_size(result.output_size or 0)}"
                    table.add_row(
                        video_file.name,
                        size_str,
                        "[green]Completed[/green]",
                        result.note,
                    )
            console.print(table)
    else:
        result = _repack_single_file(
            media,
            audio_indices,
            subtitle_indices,
            audio_langs,
            subtitle_langs,
            extra_args_list,
            output,
        )

        if config.JSON_OUTPUT:
            echo.print_json(result.to_dict())
            if result.error:
                raise Exit(f"Repack failed: {result.error}", code=1)
        else:
            if result.error:
                raise Exit(f"Repack failed: {result.error}", code=1)
            if result.skipped:
                echo.info(f"Skipped: {result.note}")
            else:
                echo.info(f"Completed: {result.input_size} -> {result.output_size}")


def _repack_single_file(
    media: Path,
    audio_indices: list[int] | None,
    subtitle_indices: list[int] | None,
    audio_langs: list[str] | None,
    subtitle_langs: list[str] | None,
    extra_args_list: list[str] | None,
    output: Path | None,
) -> "MediaResult":
    """Helper to repack a single file."""
    if output is None:
        output = media.with_suffix(".mp4")

    if output.exists() and not config.OVERWRITE_DEFAULT:
        return MediaResult(
            command="media repack",
            input=str(media),
            output=str(output),
            skipped=True,
            note="File already exists",
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    input_size = media.stat().st_size

    try:
        ffmpeg = Ffmpeg()
        ffmpeg.repack_to_mp4(
            input_file=media,
            output_file=output,
            audio_indices=audio_indices,
            subtitle_indices=subtitle_indices,
            audio_langs=audio_langs,
            subtitle_langs=subtitle_langs,
            extra_args=extra_args_list,
        )
        output_size = output.stat().st_size

        return MediaResult(
            command="media repack",
            input=str(media),
            output=str(output),
            input_size=input_size,
            output_size=output_size,
        )
    except Exception as e:
        return MediaResult(
            command="media repack",
            input=str(media),
            output=str(output),
            error=str(e),
        )


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
    stream_audio: int | None = typer.Option(
        None,
        "--stream-audio",
        help="Audio stream index (0-based ffmpeg index)",
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
    stream_subtitle: int | None = typer.Option(
        None,
        "--stream-subtitle",
        help="Subtitle stream index (0-based ffmpeg index)",
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

        Non-interactive mode (with --yes):
        $ browser-streamer --yes stream media.mkv --audio-lang jpn --subtitle-lang eng
    """
    if stream_audio is not None and audio_lang is not None:
        raise Exit("Cannot specify both --stream-audio and --audio-lang", code=1)
    if stream_subtitle is not None and subtitle_lang is not None:
        raise Exit("Cannot specify both --stream-subtitle and --subtitle-lang", code=1)

    with_nginx = server.lower() == "nginx"
    with_plex = server.lower() == "plex"

    # Determine scanning behavior:
    # - Always scan if media is a directory
    # - For single files: scan only if --scan-external is used
    # - Don't scan if specific files provided or --raw mode
    is_directory = media.is_dir()
    has_specific_files = audio_file is not None or subtitle_file is not None
    should_scan = (is_directory or scan_external) and not has_specific_files and not raw

    media = utils.resolve_path_pwd(media)

    if prepare_only:
        if raw:
            # With --raw, no conversion needed, just inform user
            echo.info(f"Media file ready for raw streaming: {media}")
        else:
            # Only prepare/convert media, don't generate streaming URLs
            # Handle batch processing for TV shows
            if media.is_dir():
                batch_info = setup_batch_processing(media)
                echo.debug(f"Batch processing info: {batch_info}")

                if batch_info:
                    batch_prepare_episodes(
                        batch_info=batch_info,
                        audio_file=audio_file,
                        audio_lang=audio_lang,
                        subtitle_file=subtitle_file,
                        subtitle_lang=subtitle_lang,
                        burn_subtitles=burn_subtitles,
                        add_subtitles_to_mp4=embed_subs,
                    )
                    return

            # Standard single file processing
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
        app()
    except PromptNeeded as e:
        echo.debug(f"PromptNeeded: {e.code}")
        if config.JSON_OUTPUT:
            result = {
                "error": e.message,
                "hint": e.hint,
                "code": e.code,
            }
            echo.print_json(result)
        else:
            echo.printc(f"Error: {e.message}", color="red", bold=True)
            if e.hint:
                echo.printc(f"Hint: {e.hint}", color="yellow")
        if config.RAISE_EXCEPTIONS:
            raise
        sys.exit(e.code)
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
        echo.printc("Set RAISE_EXCEPTIONS=true environment variable to raise exceptions")
        sys.exit(2)
