"""Smoke tests for CLI commands using CliRunner and mocked Ffmpeg."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from browser_stream.cli import app
from browser_stream.helpers import FfmpegMediaInfo, FfmpegStream


@pytest.fixture
def runner():
    """Create a CliRunner for testing."""
    return CliRunner()


@pytest.fixture
def temp_video_file():
    """Create a temporary video file."""
    with tempfile.NamedTemporaryFile(suffix=".mkv", delete=False) as f:
        f.write(b"fake video content")
        path = Path(f.name)
    yield path
    path.unlink()


@pytest.fixture
def temp_subtitle_file():
    """Create a temporary subtitle file."""
    with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as f:
        f.write(b"1\n00:00:00,000 --> 00:00:05,000\nTest subtitle\n")
        path = Path(f.name)
    yield path
    path.unlink()


@pytest.fixture
def mock_media_info():
    """Create a mock FfmpegMediaInfo with sample streams."""
    info = MagicMock(spec=FfmpegMediaInfo)
    video_stream = FfmpegStream(index=0, type="video", codec="h264", language=None)
    info.video = video_stream
    info.audios = [
        FfmpegStream(index=1, type="audio", codec="aac", language="eng"),
        FfmpegStream(index=2, type="audio", codec="mp3", language="jpn"),
    ]
    info.subtitles = [
        FfmpegStream(index=3, type="subtitle", codec="subrip", language="eng"),
        FfmpegStream(index=4, type="subtitle", codec="subrip", language="rus"),
    ]
    info.to_dict.return_value = {
        "filename": "test.mkv",
        "title": "Test Video",
        "bitrate": "5000 kb/s",
        "duration": "0:30:00",
        "comment": None,
        "streams": [
            {
                "index": 0,
                "type": "video",
                "codec": "h264",
                "title": "",
                "encoding_info": None,
                "language": None,
            },
            {
                "index": 1,
                "type": "audio",
                "codec": "aac",
                "title": "",
                "encoding_info": None,
                "language": "eng",
            },
            {
                "index": 2,
                "type": "audio",
                "codec": "mp3",
                "title": "",
                "encoding_info": None,
                "language": "jpn",
            },
            {
                "index": 3,
                "type": "subtitle",
                "codec": "subrip",
                "title": "",
                "encoding_info": None,
                "language": "eng",
            },
            {
                "index": 4,
                "type": "subtitle",
                "codec": "subrip",
                "title": "",
                "encoding_info": None,
                "language": "rus",
            },
        ],
    }
    return info


class TestMediaInfo:
    def test_media_info_json_output(self, runner, temp_video_file, mock_media_info):
        """Test `media info` with --json flag."""
        with patch("browser_stream.cli.Ffmpeg") as mock_ffmpeg_class:
            mock_instance = MagicMock()
            mock_ffmpeg_class.return_value = mock_instance
            mock_instance.get_media_info.return_value = mock_media_info

            result = runner.invoke(
                app,
                ["--json", "media", "info", str(temp_video_file)],
            )

            assert result.exit_code == 0
            output = json.loads(result.stdout)
            assert "streams" in output
            audio_streams = [s for s in output["streams"] if s["type"] == "audio"]
            assert len(audio_streams) == 2

    def test_media_info_json_only_audio(self, runner, temp_video_file, mock_media_info):
        """Test `media info` with --json and --only audio."""
        with patch("browser_stream.cli.Ffmpeg") as mock_ffmpeg_class:
            mock_instance = MagicMock()
            mock_ffmpeg_class.return_value = mock_instance
            mock_instance.get_media_info.return_value = mock_media_info

            result = runner.invoke(
                app,
                ["--json", "media", "info", str(temp_video_file), "--only", "audio"],
            )

            assert result.exit_code == 0
            output = json.loads(result.stdout)
            assert "audio" in output
            assert all(s["type"] == "audio" for s in output["audio"])


class TestMediaExtractAudio:
    def test_extract_audio_single_lang_match(self, runner, temp_video_file):
        """Test extract-audio with matching language."""
        with patch("browser_stream.cli.Ffmpeg") as mock_ffmpeg_class:
            mock_instance = MagicMock()
            mock_ffmpeg_class.return_value = mock_instance

            # Create mock info with one audio stream
            info = MagicMock(spec=FfmpegMediaInfo)
            info.audios = [
                FfmpegStream(index=1, type="audio", codec="aac", language="jpn"),
            ]
            info.subtitles = []
            info.videos = []
            mock_instance.get_media_info.return_value = info

            # Mock the extract function to create actual file
            output_file = temp_video_file.with_stem(f"{temp_video_file.stem}.jp.audio")
            output_file = output_file.with_suffix(".aac")

            def create_output(*args, **kwargs):
                output_file.write_text("fake audio data")
                return output_file

            mock_instance.extract_audio_with_convert.side_effect = create_output

            result = runner.invoke(
                app,
                [
                    "--json",
                    "media",
                    "extract-audio",
                    str(temp_video_file),
                    "--lang",
                    "jpn",
                ],
            )

            assert result.exit_code == 0
            output = json.loads(result.stdout)
            assert output["command"] == "media extract-audio"

            if output_file.exists():
                output_file.unlink()


class TestMediaRepack:
    def test_repack_happy_path(self, runner, temp_video_file, mock_media_info):
        """Test `media repack` happy path with mocked ffmpeg."""
        output_file = temp_video_file.with_suffix(".mp4")

        with patch("browser_stream.cli.Ffmpeg") as mock_ffmpeg_class:
            mock_instance = MagicMock()
            mock_ffmpeg_class.return_value = mock_instance
            mock_instance.get_media_info.return_value = mock_media_info

            def create_output(*args, **kwargs):
                output_file.write_text("fake video data")
                return output_file

            mock_instance.repack_to_mp4.side_effect = create_output

            result = runner.invoke(
                app,
                [
                    "--json",
                    "media",
                    "repack",
                    str(temp_video_file),
                    "--audio-lang",
                    "eng",
                ],
            )

            assert result.exit_code == 0
            output = json.loads(result.stdout)
            assert output["command"] == "media repack"
            assert output["input"] == str(temp_video_file)

            if output_file.exists():
                output_file.unlink()

    def test_repack_existing_output_errors(self, runner, temp_video_file):
        """Test `media repack` with existing output errors with --overwrite hint."""
        output_file = temp_video_file.with_suffix(".mp4")
        # Create the output file
        output_file.write_text("existing content")

        result = runner.invoke(
            app,
            [
                "--json",
                "media",
                "repack",
                str(temp_video_file),
                "--audio-lang",
                "eng",
            ],
        )

        assert result.exit_code != 0
        output = json.loads(result.stdout)
        assert output["error"] is not None
        assert "--overwrite" in output["error"]
        assert output["command"] == "media repack"

        # Clean up
        output_file.unlink()

    def test_repack_with_overwrite(self, runner, temp_video_file, mock_media_info):
        """Test `media repack` with --overwrite flag."""
        output_file = temp_video_file.with_suffix(".mp4")
        output_file.write_text("existing content")

        with patch("browser_stream.cli.Ffmpeg") as mock_ffmpeg_class:
            mock_instance = MagicMock()
            mock_ffmpeg_class.return_value = mock_instance
            mock_instance.repack_to_mp4.return_value = output_file

            result = runner.invoke(
                app,
                [
                    "--json",
                    "--overwrite",
                    "media",
                    "repack",
                    str(temp_video_file),
                    "--audio-lang",
                    "eng",
                ],
            )

            assert result.exit_code == 0
            output = json.loads(result.stdout)
            assert output["skipped"] is False
            mock_instance.repack_to_mp4.assert_called()

        # Clean up
        output_file.unlink()


class TestMediaConvertSubs:
    def test_convert_subs_to_vtt(self, runner, temp_subtitle_file):
        """Test `media convert-subs` to VTT format."""
        with (
            patch("browser_stream.cli.Ffmpeg") as mock_ffmpeg_class,
            patch("browser_stream.cli.FS") as mock_fs_class,
        ):
            mock_ffmpeg = MagicMock()
            mock_ffmpeg_class.return_value = mock_ffmpeg
            mock_fs = MagicMock()
            mock_fs_class.return_value = mock_fs

            # Mock the output VTT file creation
            vtt_file = temp_subtitle_file.with_suffix(".vtt")

            def create_vtt(*args, **kwargs):
                vtt_file.write_text(
                    "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nTest subtitle"
                )
                return vtt_file

            mock_ffmpeg.convert_subtitle.side_effect = create_vtt

            result = runner.invoke(
                app,
                ["--json", "media", "convert-subs", str(temp_subtitle_file)],
            )

            assert result.exit_code == 0
            output = json.loads(result.stdout)
            assert output["command"] == "media convert-subs"

            if vtt_file.exists():
                vtt_file.unlink()


class TestLogLevel:
    def test_log_level_error_suppresses_output(
        self, runner, temp_video_file, mock_media_info
    ):
        """Test that --log-level error suppresses log output."""
        with patch("browser_stream.cli.Ffmpeg") as mock_ffmpeg_class:
            mock_instance = MagicMock()
            mock_ffmpeg_class.return_value = mock_instance
            mock_instance.get_media_info.return_value = mock_media_info

            result = runner.invoke(
                app,
                [
                    "--json",
                    "--log-level",
                    "error",
                    "media",
                    "info",
                    str(temp_video_file),
                ],
            )

            assert result.exit_code == 0
            # Should have JSON in stdout and minimal/no info in stderr
            output = json.loads(result.stdout)
            assert "streams" in output


class TestExitCodes:
    def test_json_output_on_error(self, runner, temp_video_file):
        """Test that JSON output is produced even on errors."""
        with patch("browser_stream.cli.Ffmpeg") as mock_ffmpeg_class:
            mock_instance = MagicMock()
            mock_ffmpeg_class.return_value = mock_instance

            # Create mock with no matching streams
            info = MagicMock(spec=FfmpegMediaInfo)
            info.audios = []
            info.subtitles = []
            info.videos = []
            mock_instance.get_media_info.return_value = info

            result = runner.invoke(
                app,
                [
                    "--json",
                    "media",
                    "extract-audio",
                    str(temp_video_file),
                    "--lang",
                    "jpn",
                ],
            )

            assert result.exit_code != 0
