from pathlib import Path
from unittest.mock import MagicMock, patch

import typer
from typer.testing import CliRunner

from browser_stream.cli import app, config_command


class TestCLI:
    """Test CLI application functionality"""

    def setup_method(self):
        """Set up test fixtures"""
        # Force disable colors by patching rich console
        self.console_patch = patch("rich.console.Console.is_terminal", return_value=False)
        self.console_patch.start()
        self.runner = CliRunner(env={"NO_COLOR": "1", "TERM": "dumb"})

    def teardown_method(self):
        """Clean up test fixtures"""
        self.console_patch.stop()

    def test_app_initialization(self):
        """Test that CLI app is properly initialized"""
        assert isinstance(app, typer.Typer)
        # Test that help shows the expected content
        result = self.runner.invoke(app, ["--help"])
        assert "browser-streamer" in result.stdout

    def test_help_command(self):
        """Test help command shows proper information"""
        result = self.runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "browser-streamer" in result.stdout
        assert "A CLI tool to prepare and manage media" in result.stdout

    @patch("browser_stream.cli.FS")
    @patch("browser_stream.cli.echo")
    def test_config_command_reset(self, mock_echo, mock_fs_class):
        """Test config reset functionality"""
        mock_fs = MagicMock()
        mock_fs_class.return_value = mock_fs

        result = self.runner.invoke(app, ["config", "--reset"])

        assert result.exit_code == 0
        mock_fs.remove_file.assert_called_once()
        mock_echo.info.assert_called_with("Configuration reset complete")

    @patch("browser_stream.cli.conf")
    @patch("browser_stream.cli.echo")
    def test_config_command_show(self, mock_echo, mock_conf):
        """Test config show functionality"""
        mock_conf.to_dict.return_value = {"test": "config"}

        result = self.runner.invoke(app, ["config"])

        assert result.exit_code == 0
        mock_conf.to_dict.assert_called_once()
        mock_echo.info.assert_called()
        mock_echo.print_json.assert_called_with({"test": "config"})

    def test_setup_subcommand_exists(self):
        """Test setup subcommand is available"""
        result = self.runner.invoke(app, ["setup", "--help"])

        assert result.exit_code == 0
        assert "Setup server (Nginx / Plex)" in result.stdout

    def test_media_subcommand_exists(self):
        """Test media subcommand is available"""
        result = self.runner.invoke(app, ["media", "--help"])

        assert result.exit_code == 0
        assert "Media helpers" in result.stdout

    def test_nginx_command_exists(self):
        """Test nginx setup command exists"""
        result = self.runner.invoke(app, ["setup", "nginx", "--help"])

        assert result.exit_code == 0
        # Should show nginx-specific options
        assert "media-dir" in result.stdout or "media_dir" in result.stdout

    @patch("browser_stream.cli.conf")
    def test_nginx_command_default_values(self, mock_conf):
        """Test nginx command accepts expected parameters"""
        mock_conf.media_dir = Path("/default/media")
        mock_conf.ipv6 = False
        mock_conf.ipv4 = True
        mock_conf.nginx_port = 32000

        # Test that the command has the expected options
        result = self.runner.invoke(app, ["setup", "nginx", "--help"])
        assert result.exit_code == 0
        # Verify specific nginx options are documented
        assert "--media-dir" in result.stdout or "media-dir" in result.stdout
        assert "--port" in result.stdout

    def test_no_args_shows_help(self):
        """Test that running with no args shows help"""
        result = self.runner.invoke(app, [])

        # Should show help due to no_args_is_help=True (may exit with code 0 or 2)
        assert result.exit_code in [0, 2]  # Different typer versions may vary
        assert "Usage:" in result.stdout or "browser-streamer" in result.stdout

    def test_invalid_command(self):
        """Test invalid command shows error"""
        result = self.runner.invoke(app, ["nonexistent"])

        assert result.exit_code != 0
        # Different error messages possible
        assert (
            "No such command" in result.stdout
            or "Usage:" in result.stdout
            or "Error" in result.stdout
            or result.exit_code == 2
        )


class TestConfigCommand:
    """Test config command functionality separately"""

    @patch("browser_stream.cli.Path")
    @patch("browser_stream.cli.FS")
    @patch("browser_stream.cli.echo")
    def test_config_reset_removes_file(self, mock_echo, mock_fs_class, mock_path):
        """Test config reset removes the config file"""
        mock_fs = MagicMock()
        mock_fs_class.return_value = mock_fs
        mock_config_path = MagicMock()
        mock_path.return_value = mock_config_path

        config_command(reset=True)

        mock_fs.remove_file.assert_called_once_with(mock_config_path)
        mock_echo.info.assert_called_with("Configuration reset complete")

    @patch("browser_stream.cli.conf")
    @patch("browser_stream.cli.echo")
    def test_config_show_prints_json(self, mock_echo, mock_conf):
        """Test config show prints JSON configuration"""
        test_config = {
            "nginx_secret": "test_secret",
            "media_dir": "/test/path",
            "nginx_port": 8080,
        }
        mock_conf.to_dict.return_value = test_config

        config_command(reset=False)

        mock_echo.info.assert_called()  # Should print config path
        mock_echo.print_json.assert_called_with(test_config)


class TestCommandLineIntegration:
    """Test command line integration scenarios"""

    def setup_method(self):
        """Set up test fixtures"""
        # Force disable colors by patching rich console
        self.console_patch = patch("rich.console.Console.is_terminal", return_value=False)
        self.console_patch.start()
        self.runner = CliRunner(env={"NO_COLOR": "1", "TERM": "dumb"})

    def teardown_method(self):
        """Clean up test fixtures"""
        self.console_patch.stop()

    def test_context_settings_applied(self):
        """Test that context settings are properly applied"""
        # Test help options are available
        result = self.runner.invoke(app, ["-h"])
        assert result.exit_code == 0
        assert "Usage:" in result.stdout

        result = self.runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.stdout

    @patch("browser_stream.cli.conf")
    def test_config_path_display(self, mock_conf):
        """Test config path is displayed in config command"""
        mock_conf.to_dict.return_value = {}

        with patch("browser_stream.cli.echo") as mock_echo:
            config_command(reset=False)

            # Should call echo.info at least once (for config path)
            mock_echo.info.assert_called()


class TestAppConfiguration:
    """Test application configuration and setup"""

    def test_typer_configuration(self):
        """Test Typer app is configured correctly by testing behavior"""
        runner = CliRunner()

        # Test that no args shows help (no_args_is_help behavior)
        result = runner.invoke(app, [])
        assert result.exit_code in [0, 2]  # May vary by typer version
        assert "Usage:" in result.stdout or "browser-streamer" in result.stdout

        # Test that both -h and --help work (context settings)
        result_h = runner.invoke(app, ["-h"])
        assert result_h.exit_code == 0

        result_help = runner.invoke(app, ["--help"])
        assert result_help.exit_code == 0

    def test_subapps_registered(self):
        """Test that sub-applications are registered"""
        runner = CliRunner()

        # Test that setup and media subcommands exist
        result = runner.invoke(app, ["--help"])
        assert "setup" in result.stdout
        assert "media" in result.stdout
