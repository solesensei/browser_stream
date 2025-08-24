from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from browser_stream import (
    BatchProcessingInfo,
    BatchProcessingSettings,
    StreamMedia,
    build_stream_url_nginx,
    build_stream_url_plex,
    is_tv_show_directory,
    select_video,
)
from browser_stream.helpers import Exit


class TestStreamUrlBuilding:
    """Test stream URL building functionality"""

    @patch("browser_stream.conf")
    def test_build_stream_url_nginx_success(self, mock_conf):
        """Test successful nginx URL building"""
        mock_conf.nginx_secret = "test_secret"
        mock_conf.nginx_domain_name = "example.com"
        mock_conf.nginx_port = 8080
        mock_conf.media_dir = Path("/media")

        media_file = Path("/media/videos/movie.mp4")

        result = build_stream_url_nginx(media_file)

        assert "example.com:8080" in result
        assert "test_secret" in result
        assert "videos/movie.mp4" in result
        assert result.startswith("https://")

    @patch("browser_stream.conf")
    def test_build_stream_url_nginx_missing_secret(self, mock_conf):
        """Test nginx URL building fails without secret"""
        mock_conf.nginx_secret = None
        mock_conf.nginx_domain_name = "example.com"
        mock_conf.nginx_port = 8080
        mock_conf.media_dir = Path("/media")

        media_file = Path("/media/videos/movie.mp4")

        with pytest.raises(Exit) as exc_info:
            build_stream_url_nginx(media_file)

        assert "Nginx secret not found" in exc_info.value.message

    @patch("browser_stream.conf")
    def test_build_stream_url_nginx_missing_domain(self, mock_conf):
        """Test nginx URL building fails without domain"""
        mock_conf.nginx_secret = "test_secret"
        mock_conf.nginx_domain_name = None
        mock_conf.nginx_port = 8080
        mock_conf.media_dir = Path("/media")

        media_file = Path("/media/videos/movie.mp4")

        with pytest.raises(Exit) as exc_info:
            build_stream_url_nginx(media_file)

        assert "Nginx domain name not found" in exc_info.value.message

    @patch("browser_stream.PlexAPI")
    @patch("browser_stream.conf")
    def test_build_stream_url_plex_success(self, mock_conf, mock_plex_api):
        """Test successful plex URL building"""
        mock_conf.plex_x_token = "plex_token"
        mock_conf.host_url = "http://localhost:32400"
        mock_conf.plex_server_id = "server123"

        mock_plex_instance = MagicMock()
        mock_plex_instance.get_stream_url.return_value = "http://plex.example.com/stream"
        mock_plex_api.return_value = mock_plex_instance

        media_file = Path("/media/videos/movie.mp4")

        with patch("browser_stream.utils.url_encode", return_value="encoded_url"):
            result = build_stream_url_plex(media_file)

        assert result == "encoded_url"
        mock_plex_instance.get_stream_url.assert_called_once_with(media_file)

    @patch("browser_stream.conf")
    def test_build_stream_url_plex_missing_token(self, mock_conf):
        """Test plex URL building fails without token"""
        mock_conf.plex_x_token = None
        mock_conf.host_url = "http://localhost:32400"
        mock_conf.plex_server_id = "server123"

        media_file = Path("/media/videos/movie.mp4")

        with pytest.raises(Exit) as exc_info:
            build_stream_url_plex(media_file)

        assert "Plex X-Token not found" in exc_info.value.message


class TestTvShowDetection:
    """Test TV show directory detection logic"""

    @patch("browser_stream.FS")
    def test_is_tv_show_directory_not_directory(self, mock_fs_class):
        """Test returns False for non-directory paths"""
        mock_path = MagicMock()
        mock_path.is_dir.return_value = False

        result = is_tv_show_directory(mock_path)

        assert result is False

    @patch("browser_stream.FS")
    def test_is_tv_show_directory_too_few_files(self, mock_fs_class):
        """Test returns False when less than 2 video files"""
        mock_fs = MagicMock()
        mock_fs.get_video_files.return_value = [Path("single_video.mp4")]
        mock_fs_class.return_value = mock_fs

        mock_path = MagicMock()
        mock_path.is_dir.return_value = True

        result = is_tv_show_directory(mock_path)

        assert result is False

    @patch("browser_stream.FS")
    @patch("browser_stream.echo")
    def test_is_tv_show_directory_with_episodes(self, mock_echo, mock_fs_class):
        """Test detects TV show with proper episode numbering"""
        # Create mock video files that look like episodes
        video_files = [
            Path("Show Name S01E01.mkv"),
            Path("Show Name S01E02.mkv"),
            Path("Show Name S01E03.mkv"),
            Path("Show Name S01E04.mkv"),
        ]

        mock_fs = MagicMock()
        mock_fs.get_video_files.return_value = video_files
        mock_fs_class.return_value = mock_fs

        mock_path = MagicMock()
        mock_path.is_dir.return_value = True

        result = is_tv_show_directory(mock_path)

        assert result is True

    @patch("browser_stream.FS")
    @patch("browser_stream.echo")
    def test_is_tv_show_directory_filters_stream_files(self, mock_echo, mock_fs_class):
        """Test filters out .stream files from consideration"""
        video_files = [
            Path("Episode 01.mkv"),
            Path("Episode 01.stream.mp4"),  # Should be filtered out
            Path("Episode 02.mkv"),
            Path("Episode 02.stream.mp4"),  # Should be filtered out
            Path("Episode 03.mkv"),
        ]

        mock_fs = MagicMock()
        mock_fs.get_video_files.return_value = video_files
        mock_fs_class.return_value = mock_fs

        mock_path = MagicMock()
        mock_path.is_dir.return_value = True

        result = is_tv_show_directory(mock_path)

        assert result is True

    @patch("browser_stream.FS")
    @patch("browser_stream.echo")
    def test_is_tv_show_directory_no_common_pattern(self, mock_echo, mock_fs_class):
        """Test returns False when files don't follow episode pattern"""
        video_files = [
            Path("RandomMovie1.mkv"),
            Path("CompletelyDifferentName.avi"),
            Path("AnotherRandomFile.mp4"),
        ]

        mock_fs = MagicMock()
        mock_fs.get_video_files.return_value = video_files
        mock_fs_class.return_value = mock_fs

        mock_path = MagicMock()
        mock_path.is_dir.return_value = True

        result = is_tv_show_directory(mock_path)

        assert result is False


class TestVideoSelection:
    """Test video file selection functionality"""

    @patch("browser_stream.FS")
    @patch("browser_stream.utils.select_options_interactive")
    def test_select_video_from_directory(self, mock_select, mock_fs_class):
        """Test selecting video from directory"""
        video_files = [Path("/media/video1.mp4"), Path("/media/video2.mkv")]

        mock_fs = MagicMock()
        mock_fs.get_video_files.return_value = video_files
        mock_fs_class.return_value = mock_fs

        mock_select.return_value = (1, "video2.mkv")

        # Create a real Path object for testing
        media_path = Path("/media")

        with patch.object(Path, "is_dir", return_value=True):
            result = select_video(media_path)

        assert result == video_files[1]

    @patch("browser_stream.FS")
    def test_select_video_no_files_in_directory(self, mock_fs_class):
        """Test error when no video files in directory"""
        mock_fs = MagicMock()
        mock_fs.get_video_files.return_value = []
        mock_fs_class.return_value = mock_fs

        mock_media_path = MagicMock(spec=Path)
        mock_media_path.is_dir.return_value = True

        with pytest.raises(Exit) as exc_info:
            select_video(mock_media_path)

        assert "No video files found" in exc_info.value.message

    @patch("browser_stream.FS")
    def test_select_video_direct_file(self, mock_fs_class):
        """Test selecting video file directly"""
        mock_fs = MagicMock()
        mock_fs.get_extension.return_value = "mp4"
        mock_fs_class.return_value = mock_fs

        mock_media_path = MagicMock(spec=Path)
        mock_media_path.is_dir.return_value = False

        result = select_video(mock_media_path)

        assert result == mock_media_path

    @patch("browser_stream.FS")
    def test_select_video_unsupported_file(self, mock_fs_class):
        """Test error with unsupported file type"""
        mock_fs = MagicMock()
        mock_fs.get_extension.return_value = "txt"
        mock_fs_class.return_value = mock_fs

        mock_media_path = MagicMock(spec=Path)
        mock_media_path.is_dir.return_value = False

        with pytest.raises(Exit) as exc_info:
            select_video(mock_media_path)

        assert "Unsupported video file" in exc_info.value.message


class TestDataClasses:
    """Test data classes functionality"""

    def test_batch_processing_settings_defaults(self):
        """Test BatchProcessingSettings default values"""
        settings = BatchProcessingSettings()

        assert settings.audio_stream_index is None
        assert settings.audio_lang is None
        assert settings.external_audio_file is None
        assert settings.select_subtitles is None
        assert settings.burn_subtitles is False
        assert settings.add_subtitles_to_mp4 is False

    def test_batch_processing_settings_initialization(self):
        """Test BatchProcessingSettings with custom values"""
        settings = BatchProcessingSettings(
            audio_stream_index=2,
            audio_lang="eng",
            burn_subtitles=True,
            select_subtitles=True,
        )

        assert settings.audio_stream_index == 2
        assert settings.audio_lang == "eng"
        assert settings.burn_subtitles is True
        assert settings.select_subtitles is True

    def test_batch_processing_info_creation(self):
        """Test BatchProcessingInfo creation"""
        directory = Path("/tv/show")
        episodes = [Path("/tv/show/ep1.mkv"), Path("/tv/show/ep2.mkv")]
        starting_episode = episodes[0]

        batch_info = BatchProcessingInfo(
            directory=directory,
            episodes_to_process=episodes,
            starting_episode=starting_episode,
        )

        assert batch_info.directory == directory
        assert batch_info.episodes_to_process == episodes
        assert batch_info.starting_episode == starting_episode
        assert batch_info.settings is None

    def test_stream_media_creation(self):
        """Test StreamMedia creation"""
        media_path = Path("/media/video.mp4")
        subtitle_path = Path("/media/video.srt")

        stream_media = StreamMedia(
            path=media_path,
            subtitles_burned=True,
            subtitle_path=subtitle_path,
            subtitle_lang="eng",
        )

        assert stream_media.path == media_path
        assert stream_media.subtitles_burned is True
        assert stream_media.subtitle_path == subtitle_path
        assert stream_media.subtitle_lang == "eng"

    def test_stream_media_defaults(self):
        """Test StreamMedia default values"""
        media_path = Path("/media/video.mp4")

        stream_media = StreamMedia(path=media_path)

        assert stream_media.path == media_path
        assert stream_media.subtitles_burned is False
        assert stream_media.subtitle_path is None
        assert stream_media.subtitle_lang is None
