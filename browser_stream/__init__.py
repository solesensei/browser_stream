#!/usr/local/bin/python
import dataclasses
import re
import typing as tp
from pathlib import Path

import typer

import browser_stream.config as config
import browser_stream.utils as utils
from browser_stream.echo import echo
from browser_stream.helpers import (
    FS,
    HTML,
    Exit,
    Ffmpeg,
    FfmpegMediaInfo,
    FfmpegStream,
    Nginx,
    PlexAPI,
    exit_if,
)

conf = utils.Config.load()


@dataclasses.dataclass
class BatchProcessingSettings:
    """Cached settings from first episode for batch processing"""

    audio_stream_index: int | None = None  # Index of selected audio stream
    audio_lang: str | None = None
    external_audio_file: Path | None = None
    external_audio_stream_index: int | None = None
    convert_audio_to_aac: bool | None = None

    select_subtitles: bool | None = None  # Whether to select subtitles at all
    subtitle_stream_index: int | None = None  # Index of selected subtitle stream
    subtitle_lang: str | None = None
    external_subtitle_file: Path | None = None
    convert_subtitle_to_vtt: bool | None = None  # Whether to convert subtitles to VTT
    burn_subtitles: bool = False
    add_subtitles_to_mp4: bool = False


@dataclasses.dataclass
class BatchProcessingInfo:
    """Information about TV show batch processing"""

    directory: Path
    episodes_to_process: list[Path]
    starting_episode: Path
    settings: BatchProcessingSettings | None = None  # Cached settings from first episode


@dataclasses.dataclass
class StreamMedia:
    path: Path
    subtitles_burned: bool = False
    subtitle_path: Path | None = None
    subtitle_lang: str | None = None


# Global batch processing settings cache
_batch_settings_cache: BatchProcessingSettings | None = None


def build_stream_url_nginx(
    media_file: Path,
) -> str:
    """Build stream URL for media file using Nginx server"""
    exit_if(not conf.nginx_secret, "Nginx secret not found")
    exit_if(not conf.nginx_domain_name, "Nginx domain name not found")
    exit_if(not conf.nginx_port, "Nginx port not found")
    exit_if(not conf.media_dir, "Media directory not found")
    relative_path = media_file.relative_to(conf.media_dir)
    return utils.url_encode(
        f"https://{conf.nginx_domain_name}:{conf.nginx_port}/{conf.media_dir.as_posix().lstrip('/')}/{relative_path.as_posix()}?x-token={conf.nginx_secret}"
    )


def build_stream_url_plex(
    media_file: Path,
) -> str:
    """Build stream URL for media file using Plex server"""
    exit_if(not conf.plex_x_token, "Plex X-Token not found")
    exit_if(not conf.host_url, "Host URL not found")
    exit_if(not conf.plex_server_id, "Plex server ID not found")
    plex = PlexAPI(conf.plex_x_token, conf.host_url, server_id=conf.plex_server_id)
    return utils.url_encode(plex.get_stream_url(media_file))


def is_tv_show_directory(directory: Path) -> bool:
    """Detect if directory contains multiple episodes (TV show) by finding common prefixes and episode numbers"""
    if not directory.is_dir():
        return False

    fs = FS()
    video_files = list(fs.get_video_files(directory, recursive_depth=0))

    stems = []
    for f in video_files:
        stem = f.stem
        if stem.lower() in ["video", "movie", "film"] or ".stream" in stem:
            continue
        stems.append(stem)

    if len(stems) < 2:
        return False

    echo.debug(f"Filtered file stems: {stems[:5]}...")

    normalized_stems = [re.sub(r"\s*_\s*", "_", stem) for stem in stems]

    def find_common_prefix(strings):
        if not strings:
            return ""
        min_len = min(len(s) for s in strings)
        for i in range(min_len):
            if not all(s[i] == strings[0][i] for s in strings):
                return strings[0][:i]
        return strings[0][:min_len]

    prefix = find_common_prefix(normalized_stems)
    echo.debug(f"Common prefix: '{prefix}'")

    if not prefix.strip():
        show_patterns = []
        for stem in normalized_stems:
            match = re.search(r"\d", stem)
            if match:
                potential_prefix = stem[: match.start()].rstrip("_- ")
                show_patterns.append(potential_prefix)

        if show_patterns:
            prefix = find_common_prefix(show_patterns)
            echo.debug(f"Pattern-based prefix: '{prefix}'")

    episode_numbers = set()
    for stem in normalized_stems:
        remaining_part = stem
        if prefix and len(stem) > len(prefix):
            remaining_part = stem[len(prefix) :].lstrip("_- ")

        numbers = re.findall(r"\d+", remaining_part)
        if numbers:
            try:
                episode_numbers.add(int(numbers[0]))
            except ValueError:
                continue

    echo.debug(f"Unique episode numbers found: {len(episode_numbers)}")

    return len(episode_numbers) >= len(stems) * 0.5 and len(episode_numbers) >= 2


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

        # Standard video selection
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
    scan_directory: bool = True,
) -> tuple[Path | FfmpegStream, str]:
    global _batch_settings_cache
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

    external_audios: list[tuple[Path, FfmpegStream]] = []
    if scan_directory:
        external_audio_files = sorted(fs.get_audio_files(media_file.parent))
        external_audio_files = [
            f for f in external_audio_files if f.stem.split(".", 1)[0] in media_file.stem
        ] or external_audio_files
        if len(external_audio_files) > 10:
            echo.warning(
                f"Found {len(external_audio_files)} audio files in {media_file.parent.name}. Showing only first 10"
            )
            external_audio_files = external_audio_files[:10]
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

    # Use cached settings if available (batch processing mode)
    if (
        _batch_settings_cache is not None
        and _batch_settings_cache.audio_stream_index is not None
    ):
        echo.info("Using cached audio selection from first episode")
        index = _batch_settings_cache.audio_stream_index
    else:
        # Interactive selection
        index, _ = utils.select_options_interactive(
            [f"[{a.language or '-'}] {a.title} ({a.codec})" for a in audios]
            + [
                f"{utils.bb('ext')} [{a.language or '-'}] {f.parent.name} / {a.title} ({a.codec})"
                for f, a in external_audios
            ],
            option_name="Audio",
            message="Select audio stream",
        )

        # Cache the selection if in batch mode
        if _batch_settings_cache is not None:
            _batch_settings_cache.audio_stream_index = index
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

        # Check if audio conversion is needed
        needs_conversion = audio_media_stream_selected.codec != config.BROWSER_AUDIO_CODEC

        if needs_conversion:
            # Use cached decision or ask user
            if (
                _batch_settings_cache is not None
                and _batch_settings_cache.convert_audio_to_aac is not None
            ):
                convert_audio = _batch_settings_cache.convert_audio_to_aac
                if convert_audio:
                    echo.info("Using cached decision: converting audio to AAC")
                else:
                    echo.info("Using cached decision: not converting audio")
            else:
                convert_audio = utils.confirm(
                    f"Audio codec is not {config.BROWSER_AUDIO_CODEC.upper()}: {audio_media_stream_selected.codec}. Do you want to convert it?"
                )
                # Cache the decision
                if _batch_settings_cache is not None:
                    _batch_settings_cache.convert_audio_to_aac = convert_audio
        else:
            convert_audio = False

        if needs_conversion and convert_audio:
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
    scan_directory: bool = True,
) -> tuple[Path | None, str | None]:
    global _batch_settings_cache
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

    external_subtitles: list[tuple[Path, FfmpegStream]] = []
    if scan_directory:
        external_subtitle_files = sorted(fs.get_subtitle_files(media_file.parent))
        external_subtitle_files = [
            f
            for f in external_subtitle_files
            if f.stem.split(".", 1)[0] in media_file.stem
        ] or external_subtitle_files
        if len(external_subtitle_files) > 20:
            echo.warning(
                f"Found {len(external_subtitle_files)} subtitle files in {media_file.parent.name}. Showing only first 20"
            )
            external_subtitle_files = external_subtitle_files[:20]
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

    # Check if subtitles should be selected
    select_subs = False
    if subtitles or external_subtitles:
        # Use cached decision or ask user
        if (
            _batch_settings_cache is not None
            and _batch_settings_cache.select_subtitles is not None
        ):
            select_subs = _batch_settings_cache.select_subtitles
            if select_subs:
                echo.info("Using cached decision: selecting subtitles")
            else:
                echo.info("Using cached decision: not selecting subtitles")
        else:
            select_subs = utils.confirm("Select subtitles?")
            # Cache the decision
            if _batch_settings_cache is not None:
                _batch_settings_cache.select_subtitles = select_subs

    if select_subs:
        echo.print("-" * 50)

        # Use cached subtitle selection if available
        if (
            _batch_settings_cache is not None
            and _batch_settings_cache.subtitle_stream_index is not None
        ):
            echo.info("Using cached subtitle selection from first episode")
            index = _batch_settings_cache.subtitle_stream_index
        else:
            # Interactive selection
            index, _ = utils.select_options_interactive(
                [f"[{s.language or '-'}] {s.title} ({s.codec})" for s in subtitles]
                + [
                    f"{utils.bb('ext')} [{s.language or '-'}] {f.parent.name} / {s.title} ({s.codec})"
                    for f, s in external_subtitles
                ],
                option_name="Subtitle",
                message="Select subtitle stream",
            )

            # Cache the selection
            if _batch_settings_cache is not None:
                _batch_settings_cache.subtitle_stream_index = index
        media_stream_subtitle = subtitles[index].index if index < len(subtitles) else None
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


def setup_batch_processing(media_path: Path) -> BatchProcessingInfo | None:
    """Handle TV show batch processing setup - returns BatchProcessingInfo if user wants batch processing"""
    fs = FS()
    video_files = sorted(fs.get_video_files(media_path, recursive_depth=0))

    if not video_files:
        return None

    # Check if this looks like a TV show directory
    if not is_tv_show_directory(media_path):
        return None

    echo.info(f"Detected TV show directory with {len(video_files)} episodes")

    # Ask if user wants batch processing first
    if not utils.confirm("Do you want to batch process episodes from a starting point?"):
        return None

    # Filter out processed files (stream variants) and group by format
    filtered_files = []
    for video_file in video_files:
        # Skip already processed variants (stream files)
        if ".stream" in video_file.stem:
            continue
        filtered_files.append(video_file)

    # Group filtered files by format
    format_groups = {}
    for video_file in filtered_files:
        ext = video_file.suffix.lower()
        if ext not in format_groups:
            format_groups[ext] = []
        format_groups[ext].append(video_file)

    # If multiple formats, let user choose
    selected_files = filtered_files
    if len(format_groups) > 1:
        format_options = []
        for ext, files in format_groups.items():
            format_options.append(f"{ext.upper()} ({len(files)} files)")

        format_index, _ = utils.select_options_interactive(
            format_options,
            option_name="Video Format",
            message="Multiple formats detected. Choose which format to process:",
        )

        selected_ext = list(format_groups.keys())[format_index]
        selected_files = format_groups[selected_ext]
        echo.info(
            f"Selected {selected_ext.upper()} format with {len(selected_files)} episodes"
        )

    # Let user select starting episode from the chosen format
    index, _ = utils.select_options_interactive(
        [f"{f.relative_to(media_path)}" for f in selected_files],
        option_name="Starting Episode",
        message="Select episode to start batch processing from",
    )

    selected_episode = selected_files[index]
    episodes_to_process = selected_files[index:]  # From selected to end

    echo.info(
        f"Will process {len(episodes_to_process)} episodes starting from: {selected_episode.name}"
    )
    echo.info("First episode will be used to configure settings for all episodes")

    # Return batch processing info
    return BatchProcessingInfo(
        directory=media_path,
        episodes_to_process=episodes_to_process,
        starting_episode=selected_episode,
    )


def batch_prepare_episodes(
    batch_info: BatchProcessingInfo,
    audio_file: Path | None = None,
    audio_lang: str | None = None,
    subtitle_file: Path | None = None,
    subtitle_lang: str | None = None,
    burn_subtitles: bool = False,
    add_subtitles_to_mp4: bool = False,
) -> None:
    """Batch process TV show episodes with settings from first episode"""
    global _batch_settings_cache

    echo.info("Configuring conversion settings using first episode...")

    # Enable batch mode to cache settings
    _batch_settings_cache = BatchProcessingSettings()

    # Process first episode to determine settings
    first_stream_media = prepare_file_to_stream(
        media=batch_info.starting_episode,
        audio_file=audio_file,
        audio_lang=audio_lang,
        subtitle_file=subtitle_file,
        subtitle_lang=subtitle_lang,
        burn_subtitles=burn_subtitles,
        add_subtitles_to_mp4=add_subtitles_to_mp4,
        no_scan=True,  # Don't scan for first episode since it's batch mode
    )

    echo.info(f"✅ First episode prepared: {first_stream_media.path}")

    # Get remaining episodes (skip first)
    remaining_episodes = batch_info.episodes_to_process[1:]

    if not remaining_episodes:
        echo.info("Only one episode to process.")
        _batch_settings_cache = None  # Clear cache
        return

    # Ask user confirmation for batch processing
    if not utils.confirm(
        f"Apply the same settings to {len(remaining_episodes)} remaining episodes?"
    ):
        echo.info("Batch processing cancelled.")
        _batch_settings_cache = None  # Clear cache
        return

    echo.info(f"Processing {len(remaining_episodes)} remaining episodes...")

    # Process remaining episodes with cached settings
    for i, episode in enumerate(remaining_episodes, 2):
        echo.info(
            f"Processing episode {i}/{len(batch_info.episodes_to_process)}: {episode.name}"
        )

        try:
            episode_stream_media = prepare_file_to_stream(
                media=episode,
                audio_file=audio_file,  # Use original parameters for consistency
                audio_lang=audio_lang,
                subtitle_file=subtitle_file,
                subtitle_lang=subtitle_lang,
                burn_subtitles=burn_subtitles,
                add_subtitles_to_mp4=add_subtitles_to_mp4,
                no_scan=True,  # Don't scan directory for each episode
            )
            echo.info(f"✅ Episode prepared: {episode_stream_media.path}")
        except Exception as e:
            echo.error(f"❌ Failed to process {episode.name}: {e}")

    echo.info("🎉 Batch processing completed!")
    _batch_settings_cache = None  # Clear cache after completion


@dataclasses.dataclass
class SelectedStream:
    """A stream chosen by the user, identified by type + language + position.

    ``position`` is the 0-based index within streams of the same
    *type + language* group (so it stays stable even if the absolute
    ffmpeg stream index changes between files).
    """
    stream_type: tp.Literal["audio", "subtitle"]
    language: str | None
    position: int


@dataclasses.dataclass
class RepackGroup:
    """A set of files that can be processed with the same stream selection."""
    files: list[Path]
    selected_streams: list[SelectedStream]


def _resolve_stream_indices(
    info: FfmpegMediaInfo,
    selected: list[SelectedStream],
) -> tuple[list[int], list[int]]:
    """Map ``SelectedStream`` identities to actual ffmpeg stream indices."""
    audio_indices: list[int] = []
    sub_indices: list[int] = []

    for sel in selected:
        candidates = info.audios if sel.stream_type == "audio" else info.subtitles
        matching = [s for s in candidates if s.language == sel.language]
        if sel.position < len(matching):
            idx = matching[sel.position].index
            (audio_indices if sel.stream_type == "audio" else sub_indices).append(idx)
        else:
            echo.warning(
                f"Stream not found: {sel.stream_type} {sel.language} "
                f"position {sel.position} in {info.filename.name}"
            )

    return audio_indices, sub_indices


def _selection_signature(
    info: FfmpegMediaInfo,
    selected_streams: list[SelectedStream],
) -> tuple:
    """Hashable key: stream count per (type, language) that the selection needs.

    Two files match when, for every (type, language) pair in the selection,
    they have at least as many streams as the highest position selected.
    """
    type_lang_needed: dict[tuple[str, str | None], int] = {}
    for sel in selected_streams:
        key = (sel.stream_type, sel.language)
        type_lang_needed[key] = max(type_lang_needed.get(key, 0), sel.position + 1)

    return tuple(sorted(
        (key, sum(
            1 for s in (info.audios if key[0] == "audio" else info.subtitles)
            if s.language == key[1]
        ))
        for key in type_lang_needed
    ))


def _select_streams_interactive(
    media_info: FfmpegMediaInfo,
    audio_langs: list[str],
    subtitle_langs: list[str],
) -> list[SelectedStream]:
    """Show a Rich table and let the user pick *specific* audio/subtitle streams."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title=f"Streams: {media_info.filename.name}")
    table.add_column("#", style="bold")
    table.add_column("Type")
    table.add_column("Language")
    table.add_column("Codec")
    table.add_column("Title")
    table.add_column("Info")

    for s in media_info.audios:
        table.add_row(str(s.index), "audio", s.language or "-", s.codec, s.title, s.encoding_info or "")
    for s in media_info.subtitles:
        table.add_row(str(s.index), "subtitle", s.language or "-", s.codec, s.title, s.encoding_info or "")

    console.print(table)

    selected: list[SelectedStream] = []

    # --- Audio stream selection ---
    if media_info.audios:
        audio_options = [
            f"{s.language or '-'} - {s.title} ({s.codec})"
            for s in media_info.audios
        ]
        audio_defaults = [
            i for i, s in enumerate(media_info.audios)
            if s.language and s.language in audio_langs
        ]

        chosen = utils.select_multi_options(
            options=audio_options,
            option_name="audio streams",
            message="Audio streams:",
            defaults=audio_defaults,
        )
        for idx in chosen:
            stream = media_info.audios[idx]
            position = sum(
                1 for s in media_info.audios[:idx]
                if s.language == stream.language
            )
            selected.append(SelectedStream("audio", stream.language, position))

    # --- Subtitle stream selection ---
    if media_info.subtitles:
        sub_options = [
            f"{s.language or '-'} - {s.title} ({s.codec})"
            for s in media_info.subtitles
        ]
        sub_defaults = [
            i for i, s in enumerate(media_info.subtitles)
            if s.language and s.language in subtitle_langs
        ]

        chosen = utils.select_multi_options(
            options=sub_options,
            option_name="subtitle streams",
            message="Subtitle streams:",
            defaults=sub_defaults,
            allow_none=True,
        )
        for idx in chosen:
            stream = media_info.subtitles[idx]
            position = sum(
                1 for s in media_info.subtitles[:idx]
                if s.language == stream.language
            )
            selected.append(SelectedStream("subtitle", stream.language, position))

    return selected


def confirm_repack(
    media: Path,
    audio_langs: list[str],
    subtitle_langs: list[str],
) -> list[RepackGroup]:
    """Probe files, ask for the first one, then only re-ask when streams differ.

    After the user picks specific streams for the first file, every other
    file is checked: if it has at least as many streams per (type, language)
    as the selection requires, it joins the same group silently.
    """
    ffmpeg = Ffmpeg()
    fs = FS()

    # Collect files
    if media.is_file():
        all_files = [media]
    else:
        all_files = sorted(
            f for f in fs.get_video_files(media, recursive_depth=0)
            if f.suffix.lower() != ".mp4"
        )
        if not all_files:
            echo.warning("No non-MP4 video files found")
            return []

    # Probe all files
    echo.info(f"Probing {len(all_files)} file(s)...")
    all_probed: list[tuple[Path, FfmpegMediaInfo]] = []
    for f in all_files:
        all_probed.append((f, ffmpeg.get_media_info(f)))

    # --- Ask for the first file ---
    first_file, first_info = all_probed[0]
    selected_streams = _select_streams_interactive(
        first_info, audio_langs, subtitle_langs,
    )

    # --- Group remaining files by selection-aware signature ---
    first_sig = _selection_signature(first_info, selected_streams)

    sig_groups: dict[tuple, list[tuple[Path, FfmpegMediaInfo]]] = {}
    for f, info in all_probed:
        sig = _selection_signature(info, selected_streams)
        sig_groups.setdefault(sig, []).append((f, info))

    result: list[RepackGroup] = []

    # Main group — files whose selected streams match the first file
    main_group = sig_groups.pop(first_sig, [])
    if main_group:
        if len(all_probed) > 1:
            echo.info(f"{len(main_group)}/{len(all_probed)} file(s) match the selected streams")
        result.append(RepackGroup(
            files=[f for f, _ in main_group],
            selected_streams=selected_streams,
        ))

    # Remaining groups — streams differ for the selected languages
    for sig, group_files in sig_groups.items():
        echo.print("")
        echo.info(f"Different streams in {len(group_files)} file(s):")
        for f, _ in group_files[:5]:
            echo.print(f"  {f.name}")
        if len(group_files) > 5:
            echo.print(f"  ... and {len(group_files) - 5} more")

        repr_file, repr_info = group_files[0]
        # Use the languages from the first selection as defaults
        prev_audio_langs = list(dict.fromkeys(
            s.language for s in selected_streams
            if s.stream_type == "audio" and s.language
        ))
        prev_sub_langs = list(dict.fromkeys(
            s.language for s in selected_streams
            if s.stream_type == "subtitle" and s.language
        ))
        group_streams = _select_streams_interactive(
            repr_info, prev_audio_langs, prev_sub_langs,
        )
        result.append(RepackGroup(
            files=[f for f, _ in group_files],
            selected_streams=group_streams,
        ))

    return result


@dataclasses.dataclass
class RepackResult:
    input_file: Path
    output_file: Path
    skipped: bool = False
    error: str | None = None
    note: str = ""
    input_size: int = 0
    output_size: int = 0


def repack_media_files(
    media: Path,
    audio_langs: list[str] | None = None,
    subtitle_langs: list[str] | None = None,
    selected_streams: list[SelectedStream] | None = None,
    output_dir: Path | None = None,
    dry_run: bool = False,
) -> list[RepackResult]:
    """Repack video files to MP4.

    Two modes:
    - **Language mode** (``audio_langs``/``subtitle_langs``): maps every stream
      of the given languages.  Used by the ``--yes`` CLI path.
    - **Stream mode** (``selected_streams``): maps specific streams chosen
      interactively.  Indices are resolved per file via
      :func:`_resolve_stream_indices`.
    """
    ffmpeg = Ffmpeg()
    fs = FS()

    # Collect files to process
    if media.is_file():
        files = [media]
    elif media.is_dir():
        files = sorted(
            f for f in fs.get_video_files(media, recursive_depth=0)
            if f.suffix.lower() != ".mp4"
        )
        if not files:
            echo.warning(f"No non-MP4 video files found in {media}")
            return []
    else:
        raise Exit(f"Path does not exist: {media}")

    results: list[RepackResult] = []

    for input_file in files:
        stem = input_file.stem
        input_size = input_file.stat().st_size

        dest_dir = output_dir or input_file.parent
        output_file = dest_dir / f"{stem}.mp4"

        # Skip if output already exists
        if output_file.exists():
            echo.info(f"Skipping (exists): {output_file.name}")
            results.append(RepackResult(
                input_file, output_file, skipped=True,
                note="already exists", input_size=input_size,
            ))
            continue

        # Resolve indices when using stream mode
        audio_idx: list[int] | None = None
        sub_idx: list[int] | None = None
        if selected_streams is not None:
            info = ffmpeg.get_media_info(input_file)
            audio_idx, sub_idx = _resolve_stream_indices(info, selected_streams)

        if dry_run:
            ffmpeg.print_media_info(input_file)
            echo.print("")
            echo.print(utils.bb("Planned output: ") + output_file.name)
            if audio_idx is not None:
                echo.print(utils.bb("Audio streams: ") + ", ".join(f"#{i}" for i in audio_idx))
                echo.print(utils.bb("Subtitle streams: ") + (", ".join(f"#{i}" for i in (sub_idx or [])) or "none"))
            else:
                echo.print(utils.bb("Audio langs: ") + ", ".join(audio_langs or []))
                echo.print(utils.bb("Subtitle langs: ") + ", ".join(subtitle_langs or []))
            echo.print("=" * 60)
            results.append(RepackResult(
                input_file, output_file, skipped=True,
                note="dry run", input_size=input_size,
            ))
            continue

        try:
            ffmpeg.repack_to_mp4(
                input_file=input_file,
                output_file=output_file,
                audio_langs=audio_langs,
                subtitle_langs=subtitle_langs,
                audio_indices=audio_idx,
                subtitle_indices=sub_idx,
            )
            output_size = output_file.stat().st_size
            results.append(RepackResult(
                input_file, output_file,
                input_size=input_size, output_size=output_size,
            ))
        except Exception as e:
            echo.error(f"Failed: {input_file.name}: {e}")
            results.append(RepackResult(
                input_file, output_file, error=str(e),
                input_size=input_size,
            ))

    return results


def prepare_file_to_stream(
    media: Path,
    audio_file: Path | None = None,
    audio_lang: str | None = None,
    subtitle_file: Path | None = None,
    subtitle_lang: str | None = None,
    burn_subtitles: bool = False,
    add_subtitles_to_mp4: bool = False,
    no_scan: bool = False,
) -> StreamMedia:
    global _batch_settings_cache
    fs = FS()
    ffmpeg = Ffmpeg()

    if media.is_dir():
        # For directories, just select a single video (no batch processing here)
        media_file = select_video(media)
    else:
        media_file = media

    ffmpeg.print_media_info(media_file)
    # Only scan directory if no specific files are provided and not explicitly disabled
    should_scan_directory = not no_scan and (audio_file is None and subtitle_file is None)

    selected_audio, audio_lang = select_audio(
        media_file=media_file,
        audio_file=audio_file,
        audio_lang=audio_lang,
        scan_directory=should_scan_directory,
    )
    echo.info(f"Selected audio: {selected_audio} [{audio_lang}]")
    subtitle_file, subtitle_lang = select_subtitle(
        media_file=media_file,
        subtitle_file=subtitle_file,
        subtitle_lang=subtitle_lang,
        scan_directory=should_scan_directory,
    )
    if subtitle_file:
        subtitle_file = fs.enforce_utf8(subtitle_file)
        subtitle_lang = subtitle_lang or utils.prompt_subtitles(subtitle_file)
        if not ffmpeg.get_media_info(subtitle_file).subtitles[0].language:
            new_subtitle_file = subtitle_file.with_name(
                f"{subtitle_file.stem}.{subtitle_lang}{subtitle_file.suffix}"
            )
            utils.move_file(
                subtitle_file,
                new_subtitle_file,
                overwrite=True,
            )
            subtitle_file = new_subtitle_file
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
        audio_stream=selected_audio if isinstance(selected_audio, FfmpegStream) else None,
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

    if subtitle_file and not burn_subtitles and fs.get_extension(subtitle_file) != "vtt":
        vtt_subtitle_file = subtitle_file.with_suffix(".vtt")
        if vtt_subtitle_file.exists() and utils.confirm(
            f"VTT subtitle file already exists: {vtt_subtitle_file.name}. Do you want to use it?"
        ):
            echo.info(
                f"VTT subtitle file already exists: {vtt_subtitle_file}. Using it for streaming"
            )
            subtitle_file = vtt_subtitle_file
        else:
            # Use cached decision or ask user
            if (
                _batch_settings_cache is not None
                and _batch_settings_cache.convert_subtitle_to_vtt is not None
            ):
                convert_to_vtt = _batch_settings_cache.convert_subtitle_to_vtt
                if convert_to_vtt:
                    echo.info("Using cached decision: converting subtitle to VTT")
                else:
                    echo.info("Using cached decision: not converting subtitle to VTT")
            else:
                convert_to_vtt = utils.confirm(
                    f"Subtitle file is not in VTT format: {subtitle_file.name} (supported in HTML5). Do you want to convert it?"
                )
                # Cache the decision
                if _batch_settings_cache is not None:
                    _batch_settings_cache.convert_subtitle_to_vtt = convert_to_vtt

            if convert_to_vtt:
                subtitle_file = ffmpeg.convert_subtitle_to_vtt(
                    subtitle_file, subtitle_lang
                )

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
    no_scan: bool = False,
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
            no_scan=no_scan,
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
    no_scan: bool = False,
):
    """
    Check file exists on Plex server, convert file and prints the URL to stream media file
    """
    echo.info("Prepare media file to stream with Plex")
    fs = FS()
    html = HTML()

    if not conf.plex_x_token:
        raise typer.BadParameter(
            "Plex X-Token not found, run `browser-streamer setup plex` first"
        )
    if not conf.host_url:
        raise typer.BadParameter(
            "Plex host URL not found, run `browser-streamer setup plex` first"
        )

    if media.suffix == ".html":
        raise typer.BadParameter(
            "HTML can't be used directly, use video file", param_hint="--media"
        )

    if not do_not_convert:
        stream_media = prepare_file_to_stream(
            media=media,
            audio_file=None,  # Plex doesn't support external audio files
            audio_lang=audio_lang,
            subtitle_file=subtitle_file,
            subtitle_lang=subtitle_lang,
            burn_subtitles=burn_subtitles,
            add_subtitles_to_mp4=True,  # Always embed subtitles for Plex
            no_scan=no_scan,
        )
        media = stream_media.path
        subtitle_file = stream_media.subtitle_path
        subtitle_lang = stream_media.subtitle_lang
        burn_subtitles = stream_media.subtitles_burned

    # Check if media file exists on Plex server
    try:
        plex = PlexAPI(conf.plex_x_token, conf.host_url, server_id=conf.plex_server_id)
        stream_url = plex.get_stream_url(media)
    except Exit as e:
        echo.error(f"Failed to get Plex stream URL: {e.message}")
        echo.info(
            "Make sure the media file is in a Plex library and the server is accessible"
        )
        raise

    if subtitle_file and not burn_subtitles:
        echo.info(
            f"Create HTML file with video and subtitles: {media.with_suffix('.html')}"
        )
        # For Plex, we need to use the direct stream URL, not build our own
        html_data = html.get_video_html_with_subtitles(
            video_url=stream_url,
            subtitles_url=build_stream_url_plex(subtitle_file),
            language=subtitle_lang or "Unknown",
        )
        html_file = media.with_suffix(".html")
        fs.write_file(html_file, html_data)
        echo.info(f"HTML file created: {html_file}")

    echo.info("Preparation done")
    echo.printc("Stream media file using Plex server", bold=True)
    echo.print(typer.style("File: ", bold=True) + f"'{media.as_posix()}'")
    echo.print(
        typer.style("URL: ", bold=True) + typer.style(stream_url, fg="blue", bold=True)
    )
