import pytest

from holmes.plugins.toolsets.http.http_toolset import (
    AuthConfig,
    EndpointConfig,
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
                EndpointConfig(
                    host="readwrite.example.com", methods=["GET", "POST"]
                ),
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
