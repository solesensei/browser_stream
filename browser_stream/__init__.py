#!/usr/local/bin/python
from pathlib import Path
import dataclasses
import typer

import browser_stream.utils as utils
from browser_stream.helpers import (
    FS,
    Ffmpeg,
    PlexAPI,
    Nginx,
    HTML,
    FfmpegStream,
    Exit,
)
import browser_stream.config as config
from browser_stream.echo import echo


conf = utils.Config.load()


@dataclasses.dataclass
class StreamMedia:
    path: Path
    subtitles_burned: bool = False
    subtitle_path: Path | None = None
    subtitle_lang: str | None = None


def build_stream_url_nginx(
    media_file: Path,
) -> str:
    """Build stream URL for media file using Nginx server"""
    assert conf.nginx_secret, "Nginx secret not found"
    assert conf.nginx_domain_name, "Nginx domain name not found"
    assert conf.nginx_port, "Nginx port not found"
    assert conf.media_dir, "Media directory not found"
    relative_path = media_file.relative_to(conf.media_dir)
    return utils.url_encode(
        f"https://{conf.nginx_domain_name}:{conf.nginx_port}/{conf.media_dir.as_posix().lstrip('/')}/{relative_path.as_posix()}?x-token={conf.nginx_secret}"
    )


def build_stream_url_plex(
    media_file: Path,
) -> str:
    """Build stream URL for media file using Plex server"""
    assert conf.plex_x_token, "Plex X-Token not found"
    assert conf.host_url, "Host URL not found"
    assert conf.plex_server_id, "Plex server ID not found"
    plex = PlexAPI(conf.plex_x_token, conf.host_url, server_id=conf.plex_server_id)
    return utils.url_encode(plex.get_stream_url(media_file))


def select_video(
    media_path: Path,
) -> Path:
    fs = FS()
    if media_path.is_dir():
        video_files = sorted(fs.get_video_files(media_path, recursive_depth=0))
        if not video_files:
            raise Exit(
                f"No video files found in directory: {media_path}. Check your media directory"
            )
        index, _ = utils.select_options_interactive(
            [f"{f.relative_to(media_path)}" for f in video_files],
            option_name="Video",
            message="Select video file",
        )
        return video_files[index]
    if fs.get_extension(media_path) in config.VIDEO_EXTENSIONS:
        return media_path
    raise Exit(f"Unsupported video file: {media_path}")


def get_aac_audio_path(
    media_file: Path,
    audio_lang: str,
) -> Path:
    return utils.get_file_path(
        path=media_file, codec=config.BROWSER_AUDIO_CODEC, language=audio_lang
    )


def get_media_stream_path(
    media_file: Path,
    language: str,
) -> Path:
    return utils.get_file_path(path=media_file, codec="mp4", language=language)


def select_audio(
    media_file: Path,
    audio_file: Path | None = None,
    audio_lang: str | None = None,
) -> tuple[Path | FfmpegStream, str]:
    ffmpeg = Ffmpeg()
    fs = FS()
    media_file_info = ffmpeg.get_media_info(media_file)
    audios = list(media_file_info.audios)
    if audio_file:
        audio_file = utils.resolve_path_pwd(audio_file)
        echo.info(f"Using audio file: {audio_file}")
        audio_file_info = ffmpeg.get_media_info(audio_file)
        audio = audio_file_info.audios[0]

        if audio_lang and audio.language and audio.language[:2] != audio_lang[:2]:
            echo.warning(
                f"Audio language mismatch: {audio.language} != {audio_lang}. Using audio file"
            )

        audio_lang = audio.language or audio_lang or utils.prompt_audio(audio)

        if audio.codec != config.BROWSER_AUDIO_CODEC:
            audio_file_aac = get_aac_audio_path(audio_file, audio_lang)
            if audio_file_aac.exists() and utils.confirm(
                f"{config.BROWSER_AUDIO_CODEC.upper()} audio file already exists: {audio_file_aac}. Do you want to use it (n – overwrite)?"
            ):
                return audio_file_aac, audio_lang
            if utils.confirm(
                f"Audio codec is not {config.BROWSER_AUDIO_CODEC.upper()}: {audio.codec} (supported in browsers). Do you want to convert it?"
            ):
                audio_file = ffmpeg.convert_audio(
                    audio_file, output_file=audio_file_aac, audio_lang=audio_lang
                )

        return audio_file, audio_lang

    external_audio_files = sorted(fs.get_audio_files(media_file.parent))
    external_audio_files = [
        f for f in external_audio_files if f.stem.split(".", 1)[0] in media_file.stem
    ] or external_audio_files
    if len(external_audio_files) > 10:
        echo.warning(
            f"Found {len(external_audio_files)} audio files in {media_file.parent.name}. Showing only first 10"
        )
        external_audio_files = external_audio_files[:10]
    external_audios: list[tuple[Path, FfmpegStream]] = []
    for external_audio_file_ in external_audio_files:
        audio_file_info = ffmpeg.get_media_info(external_audio_file_)
        external_audios.append((external_audio_file_, audio_file_info.audios[0]))

    if audio_lang:
        matched_internal_audios = [
            a for a in audios if a.language is None or a.language[:2] == audio_lang[:2]
        ]
        matched_external_audios = [
            (f, a)
            for f, a in external_audios
            if a.language is None or a.language[:2] == audio_lang[:2]
        ]
        if not matched_internal_audios and not matched_external_audios:
            echo.warning(f"No audio found for language: {audio_lang}")
        else:
            audios = matched_internal_audios
            external_audios = matched_external_audios

    echo.print("-" * 50)
    index, _ = utils.select_options_interactive(
        [f"[{a.language or '-'}] {a.title} ({a.codec})" for a in audios]
        + [
            f"{utils.bb('ext')} [{a.language or '-'}] {f.parent.name} / {a.title} ({a.codec})"
            for f, a in external_audios
        ],
        option_name="Audio",
        message="Select audio stream",
    )
    audio_media_stream_selected = audios[index] if index < len(audios) else None
    external_audio_file, audio_external_stream_selected = (
        external_audios[index - len(audios)] if index >= len(audios) else (None, None)
    )
    if audio_media_stream_selected:
        audio_lang = audio_media_stream_selected.language or utils.prompt_audio(
            audio_media_stream_selected
        )
        audio_aac = get_aac_audio_path(media_file, audio_lang)
        if audio_aac.exists() and utils.confirm(
            f"{config.BROWSER_AUDIO_CODEC.upper()} audio file already exists: {audio_aac.name}. Do you want to use it?"
        ):
            return audio_aac, audio_lang

        if (
            audio_media_stream_selected.codec != config.BROWSER_AUDIO_CODEC
            and utils.confirm(
                f"Audio codec is not {config.BROWSER_AUDIO_CODEC.upper()}: {audio_media_stream_selected.codec}. Do you want to convert it?"
            )
        ):
            return ffmpeg.extract_audio_with_convert(
                media_file=media_file,
                output_file=audio_aac,
                stream_index=audio_media_stream_selected.index,
                audio_lang=audio_lang,
                codec=config.BROWSER_AUDIO_CODEC,
                bitrate=config.BROWSER_AUDIO_BITRATE,
            ), audio_lang
        return audio_media_stream_selected, audio_lang
    if external_audio_file and audio_external_stream_selected:
        audio_lang = audio_external_stream_selected.language or utils.prompt_audio(
            audio_external_stream_selected
        )
        audio_aac = get_aac_audio_path(external_audio_file, audio_lang)
        if audio_aac.exists() and utils.confirm(
            f"{config.BROWSER_AUDIO_CODEC.upper()} audio file already exists: {audio_aac.name}. Do you want to use it?"
        ):
            return audio_aac, audio_lang

        if (
            audio_external_stream_selected.codec != config.BROWSER_AUDIO_CODEC
            and utils.confirm(
                f"Audio codec is not {config.BROWSER_AUDIO_CODEC.upper()}: {audio_external_stream_selected.codec}. Do you want to convert it?"
            )
        ):
            return ffmpeg.convert_audio(
                external_audio_file,
                output_file=audio_aac,
                audio_lang=audio_lang,
                codec=config.BROWSER_AUDIO_CODEC,
            ), audio_lang
        return external_audio_file, audio_lang
    raise Exit("Audio file not found")


def select_subtitle(
    media_file: Path,
    subtitle_file: Path | None = None,
    subtitle_lang: str | None = None,
) -> tuple[Path | None, str | None]:
    ffmpeg = Ffmpeg()
    fs = FS()
    media_file_info = ffmpeg.get_media_info(media_file)
    subtitles = list(media_file_info.subtitles)
    media_stream_subtitle: int | None = None
    if subtitle_file:
        subtitle_file = utils.resolve_path_pwd(subtitle_file)
        echo.info(f"Using subtitle file: {subtitle_file}")
        subtitle_file_info = ffmpeg.get_media_info(subtitle_file)
        subtitle = subtitle_file_info.subtitles[0]
        subtitle_lang = (
            subtitle_lang or subtitle.language or utils.prompt_subtitles(subtitle)
        )
        if (
            subtitle_lang
            and subtitle.language
            and subtitle.language[:2] != subtitle_lang[:2]
        ):
            echo.warning(
                f"Subtitle language mismatch: {subtitle.language} != {subtitle_lang}. Using subtitle file"
            )
        return subtitle_file, subtitle_lang

    external_subtitle_files = sorted(fs.get_subtitle_files(media_file.parent))
    external_subtitle_files = [
        f for f in external_subtitle_files if f.stem.split(".", 1)[0] in media_file.stem
    ] or external_subtitle_files
    if len(external_subtitle_files) > 20:
        echo.warning(
            f"Found {len(external_subtitle_files)} subtitle files in {media_file.parent.name}. Showing only first 20"
        )
        external_subtitle_files = external_subtitle_files[:20]
    external_subtitles: list[tuple[Path, FfmpegStream]] = []
    for external_subtitle_file_ in external_subtitle_files:
        subtitle_file_info = ffmpeg.get_media_info(external_subtitle_file_)
        external_subtitles.append(
            (external_subtitle_file_, subtitle_file_info.subtitles[0])
        )

    if subtitle_lang:
        matched_internal_subtitles = [
            s
            for s in subtitles
            if s.language is None or s.language[:2] == subtitle_lang[:2]
        ]
        matched_external_subtitles = [
            (f, s)
            for f, s in external_subtitles
            if s.language is None or s.language[:2] == subtitle_lang[:2]
        ]
        if not matched_internal_subtitles and not matched_external_subtitles:
            echo.warning(f"No subtitle found for language: {subtitle_lang}")
        else:
            subtitles = matched_internal_subtitles
            external_subtitles = matched_external_subtitles

    if (subtitles or external_subtitles) and utils.confirm("Select subtitles?"):
        echo.print("-" * 50)
        index, _ = utils.select_options_interactive(
            [f"[{s.language or '-'}] {s.title} ({s.codec})" for s in subtitles]
            + [
                f"{utils.bb('ext')} [{s.language or '-'}] {f.parent.name} / {s.title} ({s.codec})"
                for f, s in external_subtitles
            ],
            option_name="Subtitle",
            message="Select subtitle stream",
        )
        media_stream_subtitle = (
            subtitles[index].index if index < len(subtitles) else None
        )
        external_subtitle_file, subtitle_external_stream = (
            external_subtitles[index - len(subtitles)]
            if index >= len(subtitles)
            else (None, None)
        )
        if external_subtitle_file and subtitle_external_stream:
            subtitle_lang = subtitle_external_stream.language or utils.prompt_subtitles(
                subtitle_external_stream
            )
            return external_subtitle_file, subtitle_lang

        if media_stream_subtitle is not None:
            subtitle_lang = subtitles[index].language or utils.prompt_subtitles(
                subtitles[index]
            )
            subtitle_file = ffmpeg.extract_subtitle(
                media_file,
                media_stream_subtitle,
                subtitle_lang=subtitle_lang,
            )
            return subtitle_file, subtitle_lang
        raise RuntimeError("Should not reach this point")
    return None, None


def get_matched_media_stream_mp4(
    media_file: Path,
    audio_lang: str,
    audio_file: Path | None = None,
    audio_stream: FfmpegStream | None = None,
    subtitle_file: Path | None = None,
    subtitle_lang: str | None = None,
    burn_subtitles: bool = False,
) -> Path | None:
    ffmpeg = Ffmpeg()
    fs = FS()

    def _matched_info(file: Path) -> bool:
        media_info = ffmpeg.get_media_info(file)
        media_audio = media_info.audios[0]
        media_subtitle = media_info.subtitles[0] if media_info.subtitles else None
        if burn_subtitles:
            if lang := media_info.get_burned_subtitles_lang():
                if lang != subtitle_lang:
                    echo.debug(
                        f"Match {file.name} | Burned subtitle language mismatch: {lang} != {subtitle_lang}"
                    )
                    return False
            else:
                echo.debug(f"Match {file.name} | Burned subtitles not found")
                return False
        if audio_stream and media_audio.codec != audio_stream.codec:
            echo.debug(
                f"Match {file.name} | Audio codec mismatch: {media_audio.codec} != {audio_stream.codec}"
            )
            return False
        if audio_file and (audio_file_info := ffmpeg.get_media_info(audio_file)):
            if media_audio != audio_file_info.audios[0].codec:
                echo.debug(
                    f"Match {file.name} | Audio file codec mismatch: {media_audio} != {audio_file_info.audios[0].codec}"
                )
                return False
        if subtitle_file:
            if not media_subtitle:
                echo.debug(f"Match {file.name} | Subtitle file not found")
                return False
            if media_subtitle.language != subtitle_lang:
                echo.debug(
                    f"Match {file.name} | Subtitle language mismatch: {media_subtitle.language} != {subtitle_lang}"
                )
                return False
        return True

    if fs.get_extension(media_file) == "mp4" and _matched_info(media_file):
        return media_file

    output_file = get_media_stream_path(media_file, language=audio_lang)
    if output_file.exists() and _matched_info(output_file):
        return output_file

    return None


def prepare_file_to_stream(
    media: Path,
    audio_file: Path | None = None,
    audio_lang: str | None = None,
    subtitle_file: Path | None = None,
    subtitle_lang: str | None = None,
    burn_subtitles: bool = False,
    add_subtitles_to_mp4: bool = False,
) -> StreamMedia:
    fs = FS()
    ffmpeg = Ffmpeg()

    if media.is_dir():
        media_file = select_video(media)
    else:
        media_file = media

    ffmpeg.print_media_info(media_file)
    selected_audio, audio_lang = select_audio(
        media_file=media_file,
        audio_file=audio_file,
        audio_lang=audio_lang,
    )
    echo.info(f"Selected audio: {selected_audio} [{audio_lang}]")
    subtitle_file, subtitle_lang = select_subtitle(
        media_file=media_file,
        subtitle_file=subtitle_file,
        subtitle_lang=subtitle_lang,
    )
    if subtitle_file:
        subtitle_file = fs.enforce_utf8(subtitle_file)
        subtitle_lang = subtitle_lang or utils.prompt_subtitles(subtitle_file)
        if not ffmpeg.get_media_info(subtitle_file).subtitles[0].language:
            ffmpeg.set_subtitle_language(subtitle_file, subtitle_lang)
        echo.info(f"Selected subtitle: {subtitle_file} [{subtitle_lang}]")

    if burn_subtitles and not subtitle_file:
        raise Exit("Subtitles not found for burning")

    if add_subtitles_to_mp4 and not subtitle_file:
        raise Exit("Subtitles not found for adding to MP4")

    output_file = get_media_stream_path(media_file, language=audio_lang)
    matched_media = get_matched_media_stream_mp4(
        media_file=media_file,
        audio_file=audio_file,
        audio_lang=audio_lang,
        audio_stream=selected_audio
        if isinstance(selected_audio, FfmpegStream)
        else None,
        subtitle_file=subtitle_file if add_subtitles_to_mp4 else None,
        subtitle_lang=subtitle_lang,
        burn_subtitles=burn_subtitles,
    )

    if matched_media:
        echo.info(f"Found matched media file: {matched_media.name}")
        media_file = matched_media
    elif not output_file.exists() or utils.confirm(
        f"File already exists: {output_file.name}, do you want to overwrite it?"
    ):
        media_file = ffmpeg.convert_to_mp4(
            media_file,
            output_file,
            audio_file=selected_audio if isinstance(selected_audio, Path) else None,
            audio_stream=selected_audio.index
            if isinstance(selected_audio, FfmpegStream)
            else None,
            audio_lang=audio_lang,
            subtitle_file=subtitle_file
            if burn_subtitles or add_subtitles_to_mp4
            else None,
            subtitle_lang=subtitle_lang,
            burn_subtitles=burn_subtitles,
        )
    else:
        echo.info(f"Using existing file: {output_file.name}")
        media_file = output_file

    if (
        subtitle_file
        and not burn_subtitles
        and fs.get_extension(subtitle_file) != "vtt"
    ):
        vtt_subtitle_file = subtitle_file.with_suffix(".vtt")
        if vtt_subtitle_file.exists() and utils.confirm(
            f"VTT subtitle file already exists: {vtt_subtitle_file.name}. Do you want to use it?"
        ):
            echo.info(
                f"VTT subtitle file already exists: {vtt_subtitle_file}. Using it for streaming"
            )
            subtitle_file = vtt_subtitle_file
        elif utils.confirm(
            f"Subtitle file is not in VTT format: {subtitle_file.name} (supported in HTML5). Do you want to convert it?"
        ):
            subtitle_file = ffmpeg.convert_subtitle_to_vtt(subtitle_file, subtitle_lang)

    return StreamMedia(
        path=media_file,
        subtitles_burned=burn_subtitles,
        subtitle_path=subtitle_file,
        subtitle_lang=subtitle_lang,
    )


def stream_nginx(
    media: Path,
    audio_file: Path | None = None,
    audio_lang: str | None = None,
    subtitle_file: Path | None = None,
    subtitle_lang: str | None = None,
    burn_subtitles: bool = False,
    add_subtitles_to_mp4: bool = False,
    do_not_convert: bool = False,
):
    """
    Check Nginx configuration, convert file and prints the URL to stream media file
    """
    echo.info("Prepare media file to stream with Nginx")
    fs = FS()
    html = HTML()

    if not conf.nginx_secret:
        raise typer.BadParameter(
            "Nginx configuration not found, run `browser-streamer setup nginx` first"
        )
    if not conf.media_dir:
        raise typer.BadParameter(
            "Media directory not found, run `browser-streamer setup nginx` first"
        )
    if not media.as_posix().startswith(conf.media_dir.as_posix()):
        raise typer.BadParameter(
            f"Media file must be in media directory: {conf.media_dir}. Found: {media.as_posix()}",
            param_hint="--media",
        )
    if media.suffix == ".html":
        raise typer.BadParameter(
            "HTML can't be used directly, use video file", param_hint="--media"
        )

    if conf.nginx_allow_index:
        echo.warning(
            "Directory listing is enabled in Nginx configuration (allow_index=true). That means anyone can navigate through your media files"
        )

    if not do_not_convert:
        stream_media = prepare_file_to_stream(
            media=media,
            audio_file=audio_file,
            audio_lang=audio_lang,
            subtitle_file=subtitle_file,
            subtitle_lang=subtitle_lang,
            burn_subtitles=burn_subtitles,
            add_subtitles_to_mp4=add_subtitles_to_mp4,
        )
        media = stream_media.path
        subtitle_file = stream_media.subtitle_path
        subtitle_lang = stream_media.subtitle_lang
        burn_subtitles = stream_media.subtitles_burned

    if subtitle_file and not burn_subtitles:
        echo.info(
            f"Create HTML file with video and subtitles: {media.with_suffix('.html')}"
        )
        html_data = html.get_video_html_with_subtitles(
            video_url=build_stream_url_nginx(media),
            subtitles_url=build_stream_url_nginx(subtitle_file),
            language=subtitle_lang or "Unknown",
        )
        media = media.with_suffix(".html")
        fs.write_file(media, html_data)

    echo.info("Preparation done")
    echo.printc("Stream media file using Nginx server", bold=True)
    echo.print(typer.style("File: ", bold=True) + f"'{media.as_posix()}'")
    echo.print(
        typer.style("URL: ", bold=True)
        + typer.style(build_stream_url_nginx(media), fg="blue", bold=True)
    )
    echo.print(
        typer.style(
            "\nDo not forget to update token with `browser-streamer setup nginx --update-token`",
            bold=True,
            fg="yellow",
        )
        + typer.style(" (after streaming media file)", fg=typer.colors.BLACK)
    )
    if conf.nginx_allow_index:
        echo.warning(
            "Directory listing is enabled in Nginx configuration (allow_index=true). That means anyone can navigate through your media files"
        )
        echo.printc(
            "Disable directory listing with `browser-streamer setup nginx --no-allow-index`",
            fg="red",
        )


def stream_plex(
    media: Path,
    audio_lang: str | None = None,
    subtitle_file: Path | None = None,
    subtitle_lang: str | None = None,
    burn_subtitles: bool = False,
    do_not_convert: bool = False,
):
    """
    Check file exists on Plex server, convert file and prints the URL to stream media file
    """
    echo.info("Prepare media file to stream with Plex")
    fs = FS()
    ffmpeg = Ffmpeg()
    html = HTML()

    if not conf.plex_x_token:
        raise typer.BadParameter(
            "Plex X-Token not found, run `browser-streamer setup plex` first"
        )
