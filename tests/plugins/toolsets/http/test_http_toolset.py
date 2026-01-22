from unittest.mock import Mock, patch

import pytest
import requests  # type: ignore

from holmes.core.tools import StructuredToolResultStatus, ToolInvokeContext
from holmes.plugins.toolsets.http.http_toolset import (
    AuthConfig,
    EndpointConfig,
    HttpRequest,
    HttpToolset,
    HttpToolsetConfig,
)


class TestAuthConfig:
    def test_basic_auth_valid(self):
        auth = AuthConfig(type="basic", username="user", password="pass")
        assert auth.type == "basic"
        assert auth.username == "user"
        assert auth.password == "pass"

    def test_basic_auth_missing_username(self):
        with pytest.raises(ValueError, match="Basic auth requires"):
            AuthConfig(type="basic", password="pass")

    def test_basic_auth_missing_password(self):
        with pytest.raises(ValueError, match="Basic auth requires"):
            AuthConfig(type="basic", username="user")

    def test_bearer_auth_valid(self):
        auth = AuthConfig(type="bearer", token="mytoken")
        assert auth.type == "bearer"
        assert auth.token == "mytoken"

    def test_bearer_auth_missing_token(self):
        with pytest.raises(ValueError, match="Bearer auth requires"):
            AuthConfig(type="bearer")

    def test_header_auth_valid(self):
        auth = AuthConfig(type="header", name="X-API-Key", value="secret")
        assert auth.type == "header"
        assert auth.name == "X-API-Key"
        assert auth.value == "secret"

    def test_header_auth_missing_name(self):
        with pytest.raises(ValueError, match="Header auth requires"):
            AuthConfig(type="header", value="secret")

    def test_header_auth_missing_value(self):
        with pytest.raises(ValueError, match="Header auth requires"):
            AuthConfig(type="header", name="X-API-Key")

    def test_none_auth(self):
        auth = AuthConfig(type="none")
        assert auth.type == "none"


class TestEndpointConfig:
    def test_default_values(self):
        endpoint = EndpointConfig(host="example.com")
        assert endpoint.get_hosts() == ["example.com"]
        assert endpoint.paths == ["*"]
        assert endpoint.get_methods() == ["GET"]

    def test_host_as_list(self):
        endpoint = EndpointConfig(host=["api.example.com", "api2.example.com"])
        assert endpoint.get_hosts() == ["api.example.com", "api2.example.com"]

    def test_custom_methods(self):
        endpoint = EndpointConfig(host="example.com", methods=["GET", "POST"])
        assert endpoint.get_methods() == ["GET", "POST"]

    def test_methods_normalized_to_uppercase(self):
        endpoint = EndpointConfig(host="example.com", methods=["get", "post"])
        assert endpoint.get_methods() == ["GET", "POST"]


class TestHttpToolsetHostMatching:
    @pytest.fixture
    def toolset(self):
        ts = HttpToolset()
        ts._http_config = HttpToolsetConfig(
            endpoints=[
                EndpointConfig(host="api.github.com"),
                EndpointConfig(host="*.atlassian.net"),
                EndpointConfig(host="example.com", paths=["/api/*"]),
                EndpointConfig(
                    host="argocd.mycompany.com",
                    paths=["/api/v1/*"],
                    methods=["GET", "POST"],
                ),
            ]
        )
        return ts

    def test_exact_host_match(self, toolset):
        endpoint, error = toolset.match_endpoint("https://api.github.com/repos/foo/bar")
        assert error is None
        assert endpoint is not None
        assert "api.github.com" in endpoint.get_hosts()

    def test_wildcard_host_match(self, toolset):
        endpoint, error = toolset.match_endpoint(
            "https://mycompany.atlassian.net/wiki/rest/api/content"
        )
        assert error is None
        assert endpoint is not None
        assert "*.atlassian.net" in endpoint.get_hosts()

    def test_wildcard_no_match_multiple_subdomains(self, toolset):
        # *.atlassian.net should NOT match foo.bar.atlassian.net
        endpoint, error = toolset.match_endpoint(
            "https://foo.bar.atlassian.net/wiki/rest/api/content"
        )
        assert error is not None
        assert endpoint is None

    def test_path_match(self, toolset):
        endpoint, error = toolset.match_endpoint("https://example.com/api/users")
        assert error is None
        assert endpoint is not None

    def test_path_no_match(self, toolset):
        endpoint, error = toolset.match_endpoint("https://example.com/other/path")
        assert error is not None
        assert endpoint is None

    def test_no_host_match(self, toolset):
        endpoint, error = toolset.match_endpoint("https://unknown.com/api")
        assert error is not None
        assert endpoint is None

    def test_invalid_url(self, toolset):
        endpoint, error = toolset.match_endpoint("not-a-url")
        assert error is not None
        assert endpoint is None


class TestHttpToolsetMethodCheck:
    @pytest.fixture
    def toolset(self):
        ts = HttpToolset()
        ts._http_config = HttpToolsetConfig(
            endpoints=[
                EndpointConfig(host="readonly.example.com"),  # Default GET only
                EndpointConfig(host="readwrite.example.com", methods=["GET", "POST"]),
            ]
        )
        return ts

    def test_get_allowed_by_default(self, toolset):
        endpoint, _ = toolset.match_endpoint("https://readonly.example.com/api")
        assert toolset.is_method_allowed("GET", endpoint)

    def test_post_not_allowed_by_default(self, toolset):
        endpoint, _ = toolset.match_endpoint("https://readonly.example.com/api")
        assert not toolset.is_method_allowed("POST", endpoint)

    def test_post_allowed_when_configured(self, toolset):
        endpoint, _ = toolset.match_endpoint("https://readwrite.example.com/api")
        assert toolset.is_method_allowed("POST", endpoint)

    def test_method_check_case_insensitive(self, toolset):
        endpoint, _ = toolset.match_endpoint("https://readwrite.example.com/api")
        assert toolset.is_method_allowed("post", endpoint)
        assert toolset.is_method_allowed("Post", endpoint)


class TestHttpToolsetHeaders:
    @pytest.fixture
    def toolset(self):
        ts = HttpToolset()
        ts._http_config = HttpToolsetConfig(
            endpoints=[EndpointConfig(host="example.com")]
        )
        return ts

    def test_bearer_auth_headers(self, toolset):
        endpoint = EndpointConfig(
            host="example.com", auth=AuthConfig(type="bearer", token="mytoken")
        )
        headers = toolset.build_headers(endpoint)
        assert headers["Authorization"] == "Bearer mytoken"

    def test_custom_header_auth(self, toolset):
        endpoint = EndpointConfig(
            host="example.com",
            auth=AuthConfig(type="header", name="X-API-Key", value="secret"),
        )
        headers = toolset.build_headers(endpoint)
        assert headers["X-API-Key"] == "secret"

    def test_basic_auth_not_in_headers(self, toolset):
        endpoint = EndpointConfig(
            host="example.com",
            auth=AuthConfig(type="basic", username="user", password="pass"),
        )
        headers = toolset.build_headers(endpoint)
        assert "Authorization" not in headers

    def test_basic_auth_tuple(self):
        toolset = HttpToolset()
        endpoint = EndpointConfig(
            host="example.com",
            auth=AuthConfig(type="basic", username="user", password="pass"),
        )
        auth = toolset.get_basic_auth(endpoint)
        assert auth == ("user", "pass")

    def test_extra_headers_override(self, toolset):
        endpoint = EndpointConfig(host="example.com", auth=AuthConfig(type="none"))
        headers = toolset.build_headers(endpoint, {"Accept": "text/plain"})
        assert headers["Accept"] == "text/plain"


class TestHttpToolsetPrerequisites:
    def test_valid_config(self):
        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "host": "example.com",
                        "auth": {"type": "bearer", "token": "test"},
                    }
                ]
            }
        )
        assert success is True
        assert "1 endpoint" in message

    def test_empty_endpoints(self):
        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable({"endpoints": []})
        assert success is False
        assert "No endpoints configured" in message

    def test_invalid_auth(self):
        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {"endpoints": [{"host": "example.com", "auth": {"type": "basic"}}]}
        )
        assert success is False
        assert "Failed to validate" in message


class TestHttpToolsetDefaultHeaders:
    def test_default_headers_applied(self):
        toolset = HttpToolset()
        toolset._http_config = HttpToolsetConfig(
            endpoints=[EndpointConfig(host="example.com")],
            default_headers={"X-Custom-Header": "custom-value"},
        )
        endpoint = EndpointConfig(host="example.com", auth=AuthConfig(type="none"))
        headers = toolset.build_headers(endpoint)
        assert headers["X-Custom-Header"] == "custom-value"
        # Default headers should still be present
        assert headers["Accept"] == "application/json"

    def test_extra_headers_override_default_headers(self):
        toolset = HttpToolset()
        toolset._http_config = HttpToolsetConfig(
            endpoints=[EndpointConfig(host="example.com")],
            default_headers={"X-Custom-Header": "default-value"},
        )
        endpoint = EndpointConfig(host="example.com", auth=AuthConfig(type="none"))
        headers = toolset.build_headers(
            endpoint, {"X-Custom-Header": "overridden-value"}
        )
        assert headers["X-Custom-Header"] == "overridden-value"


class TestHttpToolsetHealthCheck:
    def test_health_check_url_in_config(self):
        endpoint = EndpointConfig(
            host="example.com", health_check_url="https://example.com/health"
        )
        assert endpoint.health_check_url == "https://example.com/health"

    def test_health_check_url_default_none(self):
        endpoint = EndpointConfig(host="example.com")
        assert endpoint.health_check_url is None


class TestHttpToolsetMultipleEndpoints:
    def test_multiple_endpoints_with_different_auth(self):
        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "host": "api1.example.com",
                        "auth": {"type": "bearer", "token": "token1"},
                    },
                    {
                        "host": "api2.example.com",
                        "auth": {
                            "type": "basic",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                    {
                        "host": "api3.example.com",
                        "auth": {"type": "header", "name": "X-API-Key", "value": "key"},
                    },
                ]
            }
        )
        assert success is True
        assert "3 endpoint" in message
        assert "3 host pattern" in message

    def test_endpoint_with_multiple_hosts(self):
        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "host": ["api1.example.com", "api2.example.com"],
                        "auth": {"type": "bearer", "token": "shared-token"},
                    }
                ]
            }
        )
        assert success is True
        assert "1 endpoint" in message
        assert "2 host pattern" in message


class TestHttpRequest:
    @pytest.fixture
    def toolset(self):
        ts = HttpToolset()
        ts._http_config = HttpToolsetConfig(
            endpoints=[
                EndpointConfig(host="api.example.com", auth=AuthConfig(type="none"))
            ]
        )
        return ts

    @pytest.fixture
    def mock_context(self):
        return Mock(spec=ToolInvokeContext)

    def test_headers_must_be_dict(self, toolset, mock_context):
        """Test that headers parameter must be a JSON object, not a list."""
        tool = HttpRequest(toolset)
        # Pass a JSON array instead of object
        result = tool._invoke(
            {"url": "https://api.example.com/test", "headers": '["value1", "value2"]'},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "must be a JSON object" in result.error

    def test_headers_invalid_json(self, toolset, mock_context):
        """Test that invalid JSON in headers returns an error."""
        tool = HttpRequest(toolset)
        result = tool._invoke(
            {"url": "https://api.example.com/test", "headers": "not-valid-json"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid headers JSON" in result.error

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.get")
    def test_error_response_includes_error_field(self, mock_get, toolset, mock_context):
        """Test that non-OK responses include an error field."""
        mock_response = Mock()
        mock_response.ok = False
        mock_response.status_code = 404
        mock_response.json.return_value = {"message": "Not found"}
        mock_get.return_value = mock_response

        tool = HttpRequest(toolset)
        result = tool._invoke(
            {"url": "https://api.example.com/test"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert result.error is not None
        assert "HTTP 404" in result.error
        # Data should still be present
        assert result.data["status_code"] == 404

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.get")
    def test_success_response_no_error_field(self, mock_get, toolset, mock_context):
        """Test that OK responses don't have an error field."""
        mock_response = Mock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "test"}
        mock_get.return_value = mock_response

        tool = HttpRequest(toolset)
        result = tool._invoke(
            {"url": "https://api.example.com/test"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.error is None
        assert result.data["status_code"] == 200
        assert result.data["body"]["data"] == "test"

    def test_url_not_whitelisted(self, toolset, mock_context):
        """Test that non-whitelisted URLs are rejected."""
        tool = HttpRequest(toolset)
        result = tool._invoke(
            {"url": "https://not-whitelisted.com/test"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not in whitelist" in result.error

    def test_unsupported_method(self, toolset, mock_context):
        """Test that unsupported HTTP methods are rejected."""
        tool = HttpRequest(toolset)
        result = tool._invoke(
            {"url": "https://api.example.com/test", "method": "DELETE"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Unsupported HTTP method" in result.error

    def test_method_not_allowed_for_endpoint(self, toolset, mock_context):
        """Test that methods not allowed for an endpoint are rejected."""
        tool = HttpRequest(toolset)
        # The fixture endpoint only allows GET by default
        result = tool._invoke(
            {"url": "https://api.example.com/test", "method": "POST"},
            mock_context,
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not allowed for this endpoint" in result.error


class TestHttpRequestHealthCheck:
    @patch("holmes.plugins.toolsets.http.http_toolset.requests.get")
    def test_health_check_success(self, mock_get):
        """Test that health check passes when endpoint returns OK."""
        mock_response = Mock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "host": "api.example.com",
                        "auth": {"type": "bearer", "token": "test"},
                        "health_check_url": "https://api.example.com/health",
                    }
                ]
            }
        )
        assert success is True
        mock_get.assert_called_once()

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.get")
    def test_health_check_failure(self, mock_get):
        """Test that health check fails when endpoint returns error."""
        mock_response = Mock()
        mock_response.ok = False
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_get.return_value = mock_response

        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "host": "api.example.com",
                        "auth": {"type": "bearer", "token": "bad-token"},
                        "health_check_url": "https://api.example.com/health",
                    }
                ]
            }
        )
        assert success is False
        assert "Health check failed" in message
        assert "HTTP 401" in message

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.get")
    def test_health_check_connection_error(self, mock_get):
        """Test that health check fails on connection error."""
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "host": "api.example.com",
                        "auth": {"type": "bearer", "token": "test"},
                        "health_check_url": "https://api.example.com/health",
                    }
                ]
            }
        )
        assert success is False
        assert "Health check failed" in message
        assert "Connection error" in message

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.get")
    def test_health_check_timeout(self, mock_get):
        """Test that health check fails on timeout."""
        mock_get.side_effect = requests.exceptions.Timeout("Request timed out")

        toolset = HttpToolset()
        success, message = toolset.prerequisites_callable(
            {
                "endpoints": [
                    {
                        "host": "api.example.com",
                        "auth": {"type": "bearer", "token": "test"},
                        "health_check_url": "https://api.example.com/health",
                    }
                ]
            }
        )
        assert success is False
        assert "Health check failed" in message
        assert "timed out" in message

    def test_no_health_check_skips_request(self):
        """Test that endpoints without health_check_url skip the check."""
        toolset = HttpToolset()
        with patch(
            "holmes.plugins.toolsets.http.http_toolset.requests.get"
        ) as mock_get:
            success, message = toolset.prerequisites_callable(
                {
                    "endpoints": [
                        {
                            "host": "api.example.com",
                            "auth": {"type": "bearer", "token": "test"},
                            # No health_check_url
                        }
                    ]
                }
            )
            assert success is True
            mock_get.assert_not_called()
