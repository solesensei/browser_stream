import json
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from browser_stream import utils
from browser_stream.utils import bb, confirm, prompt


class TestUtilityFunctions:
    """Test utility functions in utils module"""

    @patch("typer.style")
    def test_bb_bold_formatting(self, mock_typer_style):
        """Test bb function calls typer.style with bold=True"""
        mock_typer_style.return_value = "styled_text"

        result = bb("test text")

        mock_typer_style.assert_called_once_with("test text", bold=True)
        assert result == "styled_text"

    @patch("typer.prompt")
    def test_prompt_function(self, mock_typer_prompt):
        """Test prompt function calls typer.prompt with bold message"""
        mock_typer_prompt.return_value = "user input"

        result = prompt("Enter value")

        mock_typer_prompt.assert_called_once()
        assert result == "user input"

    @patch("typer.confirm")
    def test_confirm_function_default_true(self, mock_typer_confirm):
        """Test confirm function with default True"""
        mock_typer_confirm.return_value = True

        result = confirm("Continue?")

        mock_typer_confirm.assert_called_once()
        args, kwargs = mock_typer_confirm.call_args
        assert kwargs.get("default") is True
        assert result is True

    @patch("typer.confirm")
    def test_confirm_function_with_abort(self, mock_typer_confirm):
        """Test confirm function with abort=True"""
        mock_typer_confirm.return_value = True

        result = confirm("Continue?", abort=True)

        mock_typer_confirm.assert_called_once()
        args, kwargs = mock_typer_confirm.call_args
        assert kwargs.get("abort") is True
        assert result is True

    def test_prompt_audio_with_path(self):
        """Test prompt_audio function with Path object"""
        mock_path = MagicMock(spec=Path)
        mock_path.name = "audio.mp3"

        with patch("browser_stream.utils.prompt", return_value="ENG") as mock_prompt:
            result = utils.prompt_audio(mock_path)

            mock_prompt.assert_called_once()
            assert "audio.mp3" in mock_prompt.call_args[0][0]
            assert result == "eng"  # Should be lowercased

    def test_prompt_audio_with_ffmpeg_stream_with_language(self):
        """Test prompt_audio with FfmpegStream that has language"""
        mock_stream = MagicMock()
        mock_stream.language = "Spanish"

        result = utils.prompt_audio(mock_stream)

        assert result == "spanish"

    def test_prompt_audio_with_ffmpeg_stream_no_language(self):
        """Test prompt_audio with FfmpegStream without language"""
        mock_stream = MagicMock()
        mock_stream.language = None

        with patch("browser_stream.utils.prompt", return_value="FRA") as mock_prompt:
            result = utils.prompt_audio(mock_stream)

            mock_prompt.assert_called_once()
            assert result == "fra"

    def test_prompt_subtitles_with_path(self):
        """Test prompt_subtitles function with Path object"""
        mock_path = MagicMock(spec=Path)
        mock_path.name = "subtitles.srt"

        with patch("browser_stream.utils.prompt", return_value="ENG") as mock_prompt:
            result = utils.prompt_subtitles(mock_path)

            mock_prompt.assert_called_once()
            assert "subtitles.srt" in mock_prompt.call_args[0][0]
            assert result == "eng"

    def test_url_encode(self):
        """Test URL encoding function"""
        test_url = "https://example.com/path with spaces"
        result = utils.url_encode(test_url)

        # Should encode spaces and other special characters
        assert " " not in result
        assert "https://example.com/" in result

    def test_resolve_path_pwd(self):
        """Test path resolution relative to PWD environment variable"""
        # Test absolute path (should remain unchanged)
        abs_path = Path("/absolute/path")
        result = utils.resolve_path_pwd(abs_path)
        assert result == abs_path

        # Test relative path with PWD environment variable
        with patch.dict("os.environ", {"PWD": "/test/pwd"}):
            result = utils.resolve_path_pwd(Path("relative/path"))
            # Should resolve to PWD + relative path
            assert "/test/pwd" in str(result)
            assert "relative/path" in str(result)

    def test_get_file_path_with_language(self):
        """Test get_file_path function with language parameter"""
        media_path = Path("/media/video.mkv")

        result = utils.get_file_path(media_path, codec="mp4", language="eng")

        assert result.suffix == ".mp4"
        assert "en" in result.stem  # Language is truncated to 2 chars
        assert "stream" in result.stem  # Default suffix
        assert result.parent == media_path.parent

    def test_get_file_path_language_truncation(self):
        """Test get_file_path truncates language to 2 characters"""
        media_path = Path("/media/video.mkv")

        result = utils.get_file_path(media_path, codec="mp4", language="english")

        assert result.suffix == ".mp4"
        assert "en" in result.stem  # Should be truncated from "english" to "en"
        assert "english" not in result.stem  # Full language should not be there

    def test_move_file_success(self):
        """Test successful file move"""
        with (
            patch("shutil.move") as mock_move,
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "exists", return_value=False),
        ):
            source = Path("/source/file.txt")
            dest = Path("/dest/file.txt")

            utils.move_file(source, dest)

            mock_move.assert_called_once_with(source, dest)

    def test_move_file_with_overwrite(self):
        """Test file move with overwrite when destination exists"""
        with (
            patch("shutil.move") as mock_move,
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "unlink") as mock_unlink,
        ):
            source = Path("/source/file.txt")
            dest = Path("/dest/file.txt")

            utils.move_file(source, dest, overwrite=True)

            mock_unlink.assert_called_once()
            mock_move.assert_called_once_with(source, dest)

    def test_move_file_not_a_file(self):
        """Test move_file raises error when source is not a file"""
        with patch.object(Path, "is_file", return_value=False):
            source = Path("/not/a/file")
            dest = Path("/dest/file.txt")

            with pytest.raises(ValueError, match="Source is not a file"):
                utils.move_file(source, dest)

    @patch("typer.prompt")
    def test_select_options_interactive(self, mock_typer_prompt):
        """Test interactive option selection"""
        options = ["Option 1", "Option 2", "Option 3"]
        mock_typer_prompt.return_value = "2"  # User selects second option

        index, selected = utils.select_options_interactive(
            options, option_name="Test", message="Select option"
        )

        assert index == 1  # 0-based index (user input "2" -> index 1)
        assert selected == "Option 2"
        mock_typer_prompt.assert_called_once()

    @patch("typer.prompt")
    def test_select_options_interactive_default_selection(self, mock_typer_prompt):
        """Test interactive selection with default (first option)"""
        options = ["Option 1", "Option 2"]
        mock_typer_prompt.return_value = "1"  # Default selection

        index, selected = utils.select_options_interactive(
            options, option_name="Test", message="Select option"
        )

        assert index == 0  # First option
        assert selected == "Option 1"


class TestConfig:
    """Test Config class functionality"""

    def test_config_load_from_existing_file(self):
        """Test loading configuration from existing file"""
        mock_config_data = {
            "nginx_secret": "test_secret",
            "media_dir": "/test/media",
            "nginx_port": 8080,
        }

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(mock_config_data))),
        ):
            config = utils.Config.load()

            assert config.nginx_secret == "test_secret"
            assert str(config.media_dir) == "/test/media"
            assert config.nginx_port == 8080

    def test_config_load_from_nonexistent_file(self):
        """Test loading configuration when file doesn't exist"""
        with patch("pathlib.Path.exists", return_value=False):
            config = utils.Config.load()

            # Should create default config
            assert config is not None
            assert config.nginx_secret is None

    def test_config_save(self):
        """Test saving configuration to file"""
        config = utils.Config()
        config.nginx_secret = "test_secret"
        config.media_dir = Path("/test/media")

        with (
            patch("builtins.open", mock_open()) as mock_file,
            patch("pathlib.Path.mkdir") as mock_mkdir,
        ):
            config.save()

            mock_mkdir.assert_called_once()
            mock_file.assert_called_once()

    def test_config_to_dict(self):
        """Test converting config to dictionary"""
        config = utils.Config()
        config.nginx_secret = "test_secret"
        config.media_dir = Path("/test/media")

        result = config.to_dict()

        assert isinstance(result, dict)
        assert "nginx_secret" in result
        assert result["nginx_secret"] == "test_secret"

    def test_config_initialization_with_kwargs(self):
        """Test creating config with keyword arguments"""
        config = utils.Config(
            nginx_secret="test_secret", media_dir=Path("/test/media"), nginx_port=8080
        )

        assert config.nginx_secret == "test_secret"
        assert str(config.media_dir) == "/test/media"
        assert config.nginx_port == 8080
