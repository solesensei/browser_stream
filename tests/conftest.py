"""Pytest configuration and fixtures."""

import pytest

import browser_stream.config as config


@pytest.fixture(autouse=True)
def reset_config():
    """Reset config flags before and after each test."""
    original_non_interactive = config.NON_INTERACTIVE
    original_json_output = config.JSON_OUTPUT
    original_overwrite_default = config.OVERWRITE_DEFAULT
    original_log_level = config.LOG_LEVEL

    # Reset to defaults
    config.NON_INTERACTIVE = False
    config.JSON_OUTPUT = False
    config.OVERWRITE_DEFAULT = False
    config.LOG_LEVEL = "info"

    yield

    # Restore original values
    config.NON_INTERACTIVE = original_non_interactive
    config.JSON_OUTPUT = original_json_output
    config.OVERWRITE_DEFAULT = original_overwrite_default
    config.LOG_LEVEL = original_log_level
