import os
from unittest.mock import patch

import pytest

from browser_stream import config


class TestEnvFlag:
    """Test _env_flag function behavior"""

    def test_env_flag_missing_variable_default_false(self):
        """Test _env_flag returns False when env var doesn't exist and default=False"""
        with patch.dict(os.environ, {}, clear=True):
            result = config._env_flag("NONEXISTENT_FLAG")
            assert result is False

    def test_env_flag_missing_variable_default_true(self):
        """Test _env_flag returns True when env var doesn't exist and default=True"""
        with patch.dict(os.environ, {}, clear=True):
            result = config._env_flag("NONEXISTENT_FLAG", default=True)
            assert result is True

    @pytest.mark.parametrize(
        "env_value,expected",
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("1", True),
            ("yes", True),
            ("YES", True),
            ("Yes", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("0", False),
            ("no", False),
            ("NO", False),
            ("No", False),
            ("random_text", False),
            ("", False),
            ("2", False),  # Only "1" should be True
            ("off", False),
            ("on", False),  # Only specific values should be True
        ],
    )
    def test_env_flag_various_values(self, env_value, expected):
        """Test _env_flag parsing various string values correctly"""
        with patch.dict(os.environ, {"TEST_FLAG": env_value}):
            result = config._env_flag("TEST_FLAG")
            assert result is expected

    def test_env_flag_override_default(self):
        """Test env var overrides default value"""
        with patch.dict(os.environ, {"TEST_FLAG": "false"}):
            # Even with default=True, env var should override
            result = config._env_flag("TEST_FLAG", default=True)
            assert result is False

        with patch.dict(os.environ, {"TEST_FLAG": "true"}):
            # Even with default=False, env var should override
            result = config._env_flag("TEST_FLAG", default=False)
            assert result is True


class TestEnvironmentVariableHandling:
    """Test how config module handles environment variables"""

    def test_no_extension_overlap(self):
        """Test that extension sets don't have unexpected overlaps"""
        # Video and audio should be distinct
        video_audio_overlap = config.VIDEO_EXTENSIONS & config.AUDIO_EXTENSIONS
        assert len(video_audio_overlap) == 0, f"Unexpected overlap: {video_audio_overlap}"

        # Video and subtitle should be distinct
        video_sub_overlap = config.VIDEO_EXTENSIONS & config.SUBTITLE_EXTENSIONS
        assert len(video_sub_overlap) == 0, f"Unexpected overlap: {video_sub_overlap}"
