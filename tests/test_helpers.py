from unittest.mock import MagicMock, patch

import httpx
import pytest

from browser_stream.helpers import Exit, PlexAPI, exit_if


class TestExit:
    """Test Exit exception class"""

    def test_exit_initialization(self):
        """Test Exit exception initialization"""
        exit_exc = Exit("Test message")

        assert exit_exc.message == "Test message"
        assert exit_exc.code == 1  # default code

    def test_exit_custom_code(self):
        """Test Exit exception with custom code"""
        exit_exc = Exit("Custom message", code=42)

        assert exit_exc.message == "Custom message"
        assert exit_exc.code == 42

    def test_exit_is_exception(self):
        """Test Exit is proper exception"""
        exit_exc = Exit("Test")

        assert isinstance(exit_exc, Exception)
        with pytest.raises(Exit):
            raise exit_exc


class TestExitIf:
    """Test exit_if helper function"""

    def test_exit_if_false_condition(self):
        """Test exit_if doesn't raise when condition is False"""
        # Should not raise
        exit_if(False, "Should not raise")
        exit_if(None, "Should not raise")
        exit_if(0, "Should not raise")
        exit_if("", "Should not raise")
        exit_if([], "Should not raise")

    def test_exit_if_true_condition(self):
        """Test exit_if raises when condition is True"""
        with pytest.raises(Exit) as exc_info:
            exit_if(True, "Should raise")

        assert exc_info.value.message == "Should raise"
        assert exc_info.value.code == 1

    def test_exit_if_truthy_conditions(self):
        """Test exit_if raises for various truthy conditions"""
        truthy_conditions = [1, "non-empty", [1, 2], {"key": "value"}]

        for condition in truthy_conditions:
            with pytest.raises(Exit):
                exit_if(condition, "Truthy condition")

    def test_exit_if_custom_code(self):
        """Test exit_if with custom exit code"""
        with pytest.raises(Exit) as exc_info:
            exit_if(True, "Custom code test", code=99)

        assert exc_info.value.code == 99


class TestPlexAPI:
    """Test PlexAPI class functionality"""

    def test_plex_api_initialization(self):
        """Test PlexAPI initialization"""
        plex = PlexAPI("test_token", "http://example.com:32400", "server123")

        assert plex._x_token == "test_token"
        assert plex._base_url == "http://example.com:32400"
        assert plex._server_id == "server123"

    def test_plex_api_base_url_strip(self):
        """Test base URL trailing slash is stripped"""
        plex = PlexAPI("token", "http://example.com:32400/", "server")

        assert plex._base_url == "http://example.com:32400"

    def test_plex_api_default_base_url(self):
        """Test default base URL"""
        plex = PlexAPI("token")

        assert plex._base_url == "http://localhost:32400"

    def test_from_direct_url_parsing(self):
        """Test creating PlexAPI from direct URL"""
        direct_url = "https://192-168-178-47.server123.plex.direct:32400/library/parts/2817/1681580846/file.mkv?download=1&X-Plex-Token=test_token_123"

        plex = PlexAPI.from_direct_url(direct_url)

        assert plex._x_token == "test_token_123"
        assert plex._server_id == "192-168-178-47"
        assert "plex.direct:32400" in plex._base_url

    def test_get_direct_url_success(self):
        """Test getting direct URL with server ID"""
        plex = PlexAPI("test_token", server_id="server123")

        result = plex.get_direct_url("library/metadata/123")

        expected = "http://localhost:32400/library/metadata/123?X-Plex-Token=test_token"
        assert result == expected

    def test_get_direct_url_no_server_id(self):
        """Test get_direct_url fails without server ID"""
        plex = PlexAPI("test_token")  # No server_id

        with pytest.raises(Exit) as exc_info:
            plex.get_direct_url("library/metadata/123")

        assert "Plex Server ID is not provided" in exc_info.value.message

    def test_encode_url(self):
        """Test URL encoding static method"""
        test_url = "https://example.com/path with spaces/file.mkv"

        result = PlexAPI.encode_url(test_url)

        # Should encode everything (safe="")
        assert " " not in result
        assert result != test_url  # Should be different

    @patch("httpx.request")
    def test_request_get_success(self, mock_request):
        """Test successful GET request"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"test": "data"}
        mock_response.raise_for_status.return_value = None
        mock_request.return_value = mock_response

        plex = PlexAPI("test_token")
        result = plex._request("GET", "/test/path", {"param": "value"})

        assert result == {"test": "data"}
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][0] == "GET"  # method
        assert "test/path" in call_args[0][1]  # url
        assert call_args[1]["params"]["X-Plex-Token"] == "test_token"
        assert call_args[1]["params"]["param"] == "value"

    @patch("httpx.request")
    def test_request_http_error(self, mock_request):
        """Test request with HTTP error"""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=MagicMock()
        )
        mock_request.return_value = mock_response

        plex = PlexAPI("test_token")

        with pytest.raises(httpx.HTTPStatusError):
            plex._request("GET", "/nonexistent")

    @patch("httpx.request")
    def test_get_method(self, mock_request):
        """Test _get convenience method"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": "success"}
        mock_response.raise_for_status.return_value = None
        mock_request.return_value = mock_response

        plex = PlexAPI("test_token")
        result = plex._get("/test", {"param": "value"})

        assert result == {"result": "success"}
        call_args = mock_request.call_args
        assert call_args[0][0] == "GET"

    @patch("browser_stream.helpers.PlexAPI._get")
    def test_get_libraries(self, mock_get):
        """Test get_libraries method"""
        mock_get.return_value = {"MediaContainer": {"libraries": []}}

        plex = PlexAPI("test_token")
        result = plex.get_libraries()

        assert result == {"libraries": []}
        mock_get.assert_called_once_with("/library/sections")

    @patch("browser_stream.helpers.PlexAPI._get")
    def test_get_library(self, mock_get):
        """Test get_library method"""
        mock_get.return_value = {"MediaContainer": {"items": []}}

        plex = PlexAPI("test_token")
        result = plex.get_library("123")

        assert result == {"items": []}
        mock_get.assert_called_once_with("/library/sections/123/all")

    @patch("browser_stream.helpers.PlexAPI._get")
    def test_get_metadata(self, mock_get):
        """Test get_metadata method"""
        expected_response = {"metadata": "info"}
        mock_get.return_value = expected_response

        plex = PlexAPI("test_token")
        result = plex.get_metadata("456")

        assert result == expected_response
        mock_get.assert_called_once_with("/library/metadata/456")

    @patch("browser_stream.helpers.PlexAPI._get")
    def test_get_metadata_children(self, mock_get):
        """Test get_metadata_children method"""
        expected_response = {"children": []}
        mock_get.return_value = expected_response

        plex = PlexAPI("test_token")
        result = plex.get_metadata_children("789")

        assert result == expected_response
        mock_get.assert_called_once_with("/library/metadata/789/children")

    def test_path_handling_leading_slash(self):
        """Test path handling strips leading slashes properly"""
        plex = PlexAPI("test_token", "http://example.com")

        with patch("httpx.request") as mock_request:
            mock_response = MagicMock()
            mock_response.json.return_value = {}
            mock_response.raise_for_status.return_value = None
            mock_request.return_value = mock_response

            # Both should result in the same URL
            plex._request("GET", "/test/path")
            plex._request("GET", "test/path")

            # Check both calls resulted in the same URL
            calls = mock_request.call_args_list
            assert len(calls) == 2
            assert calls[0][0][1] == calls[1][0][1]  # Same URL
