import fnmatch
import logging
import os
import re
from typing import Any, ClassVar, Dict, List, Literal, Optional, Tuple, Type, Union
from urllib.parse import urlparse

import requests  # type: ignore
from pydantic import BaseModel, Field, model_validator

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
)
from holmes.plugins.toolsets.json_filter_mixin import JsonFilterMixin

logger = logging.getLogger(__name__)


class AuthConfig(BaseModel):
    """Authentication configuration for an endpoint."""

    type: Literal["none", "basic", "bearer", "header"] = "none"
    # For basic auth
    username: Optional[str] = None
    password: Optional[str] = None
    # For bearer auth
    token: Optional[str] = None
    # For custom header auth
    name: Optional[str] = None
    value: Optional[str] = None

    @model_validator(mode="after")
    def validate_auth_fields(self):
        if self.type == "basic":
            if not self.username or not self.password:
                raise ValueError("Basic auth requires 'username' and 'password'")
        elif self.type == "bearer":
            if not self.token:
                raise ValueError("Bearer auth requires 'token'")
        elif self.type == "header":
            if not self.name or not self.value:
                raise ValueError("Header auth requires 'name' and 'value'")
        return self


class EndpointConfig(BaseModel):
    """Configuration for a single endpoint (host + optional path restrictions)."""

    host: Union[
        str, List[str]
    ]  # e.g., "*.atlassian.net" or ["api.github.com", "*.githubusercontent.com"]
    paths: List[str] = Field(
        default_factory=lambda: ["*"]
    )  # Allowed paths, default allows all
    methods: List[str] = Field(
        default_factory=lambda: ["GET"]
    )  # Allowed HTTP methods, default GET only
    auth: AuthConfig = Field(default_factory=AuthConfig)

    def get_hosts(self) -> List[str]:
        """Return hosts as a list."""
        if isinstance(self.host, str):
            return [self.host]
        return self.host

    def get_methods(self) -> List[str]:
        """Return allowed methods as uppercase list."""
        return [m.upper() for m in self.methods]


class HttpToolsetConfig(BaseModel):
    """Configuration for the HTTP toolset."""

    endpoints: List[EndpointConfig] = Field(default_factory=list)
    verify_ssl: bool = True
    timeout_seconds: int = 30
    default_headers: Dict[str, str] = Field(default_factory=dict)


class HttpToolset(Toolset):
    """Generic HTTP toolset for making requests to whitelisted endpoints."""

    config_class: ClassVar[Type[HttpToolsetConfig]] = HttpToolsetConfig

    def __init__(self):
        super().__init__(
            name="http",
            description="Generic HTTP client for making requests to whitelisted API endpoints",
            icon_url="https://cdn-icons-png.flaticon.com/512/2165/2165004.png",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/http/",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[HttpRequest(self)],
        )
        self._http_config: Optional[HttpToolsetConfig] = None
        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        """Validate the HTTP toolset configuration."""
        try:
            self._http_config = HttpToolsetConfig(**config)

            if not self._http_config.endpoints:
                return (
                    False,
                    "No endpoints configured. Add at least one endpoint with host and auth.",
                )

            # Validate each endpoint has at least one host
            for i, endpoint in enumerate(self._http_config.endpoints):
                hosts = endpoint.get_hosts()
                if not hosts:
                    return False, f"Endpoint {i} has no hosts configured."

            endpoint_count = len(self._http_config.endpoints)
            host_count = sum(len(ep.get_hosts()) for ep in self._http_config.endpoints)
            return (
                True,
                f"HTTP toolset configured with {endpoint_count} endpoint(s) covering {host_count} host pattern(s).",
            )

        except Exception as e:
            return False, f"Failed to validate HTTP configuration: {str(e)}"

    @property
    def http_config(self) -> HttpToolsetConfig:
        if self._http_config is None:
            raise RuntimeError(
                "HTTP toolset not configured. Call prerequisites_callable first."
            )
        return self._http_config

    def get_example_config(self) -> Dict[str, Any]:
        """Return an example configuration for this toolset."""
        return {
            "endpoints": [
                {
                    "host": "*.example.com",
                    "paths": ["/api/*"],
                    "methods": ["GET"],  # Default is GET only
                    "auth": {
                        "type": "bearer",
                        "token": "your-api-token",
                    },
                }
            ],
            "verify_ssl": True,
            "timeout_seconds": 30,
        }

    def match_endpoint(
        self, url: str
    ) -> Tuple[Optional[EndpointConfig], Optional[str]]:
        """
        Find the endpoint config that matches the given URL.

        Returns:
            Tuple of (matched_endpoint, error_message)
            If matched, error_message is None.
            If not matched, endpoint is None and error_message explains why.
        """
        try:
            parsed = urlparse(url)
        except Exception as e:
            return None, f"Invalid URL: {e}"

        if not parsed.scheme or not parsed.netloc:
            return None, f"Invalid URL format: {url}"

        host = parsed.netloc
        path = parsed.path or "/"

        # Find matching endpoint
        for endpoint in self.http_config.endpoints:
            for host_pattern in endpoint.get_hosts():
                if self._match_host(host, host_pattern):
                    # Host matches, check path
                    if self._match_path(path, endpoint.paths):
                        return endpoint, None
                    else:
                        # Host matched but path didn't - continue searching
                        # (another endpoint might match both)
                        pass

        # Build helpful error message
        return (
            None,
            f"URL not in whitelist. Host '{host}' with path '{path}' does not match any configured endpoint.",
        )

    def _match_host(self, host: str, pattern: str) -> bool:
        """Match a host against a pattern (supports wildcards like *.example.com)."""
        # Convert glob pattern to regex
        # *.example.com should match foo.example.com but not foo.bar.example.com
        if pattern.startswith("*."):
            # Match single subdomain level
            regex_pattern = r"^[^.]+\." + re.escape(pattern[2:]) + "$"
            return bool(re.match(regex_pattern, host, re.IGNORECASE))
        else:
            # Exact match (case-insensitive)
            return host.lower() == pattern.lower()

    def _match_path(self, path: str, patterns: List[str]) -> bool:
        """Match a path against a list of patterns."""
        for pattern in patterns:
            if pattern == "*":
                return True
            # Use fnmatch for glob-style matching
            if fnmatch.fnmatch(path, pattern):
                return True
        return False

    def is_method_allowed(self, method: str, endpoint: EndpointConfig) -> bool:
        """Check if the HTTP method is allowed for this endpoint."""
        return method.upper() in endpoint.get_methods()

    def build_headers(
        self, endpoint: EndpointConfig, extra_headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """Build request headers including auth."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        # Add default headers
        headers.update(self.http_config.default_headers)

        # Add auth headers
        auth = endpoint.auth
        if auth.type == "bearer":
            headers["Authorization"] = f"Bearer {auth.token}"
        elif auth.type == "header":
            if auth.name and auth.value:
                headers[auth.name] = auth.value

        # Add extra headers (can override defaults)
        if extra_headers:
            headers.update(extra_headers)

        return headers

    def get_basic_auth(self, endpoint: EndpointConfig) -> Optional[Tuple[str, str]]:
        """Get basic auth tuple if configured."""
        if (
            endpoint.auth.type == "basic"
            and endpoint.auth.username
            and endpoint.auth.password
        ):
            return (endpoint.auth.username, endpoint.auth.password)
        return None


class HttpRequest(Tool, JsonFilterMixin):
    """Tool for making HTTP requests to whitelisted endpoints."""

    def __init__(self, toolset: HttpToolset):
        base_params = {
            "url": ToolParameter(
                description="The full URL to request (must match a whitelisted endpoint)",
                type="string",
                required=True,
            ),
            "method": ToolParameter(
                description="HTTP method: GET (default) or POST (only if allowed for the endpoint)",
                type="string",
                required=False,
            ),
            "body": ToolParameter(
                description="JSON request body for POST requests",
                type="string",
                required=False,
            ),
            "headers": ToolParameter(
                description="Additional HTTP headers as JSON object (optional)",
                type="string",
                required=False,
            ),
        }

        # Add JsonFilterMixin parameters
        parameters = JsonFilterMixin.extend_parameters(base_params)

        super().__init__(
            name="http_request",
            description=(
                "Make HTTP requests to whitelisted API endpoints. "
                "Use this tool for APIs that don't have a dedicated toolset. "
                "IMPORTANT: Always prefer more specific toolsets when available "
                "(e.g., use grafana tools for Grafana, prometheus tools for Prometheus). "
                "This tool is for general-purpose HTTP access to configured endpoints."
            ),
            parameters=parameters,
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        url = params.get("url", "")
        method = params.get("method", "GET").upper()
        body = params.get("body")
        extra_headers_str = params.get("headers")

        # Validate URL against whitelist
        endpoint, error = self._toolset.match_endpoint(url)
        if error or endpoint is None:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error or "URL not matched",
                params=params,
                url=url,
            )

        # Validate method
        if method not in ("GET", "POST"):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unsupported HTTP method: {method}. Only GET and POST are supported.",
                params=params,
                url=url,
            )

        # Check if method is allowed for this endpoint
        if not self._toolset.is_method_allowed(method, endpoint):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Method {method} not allowed for this endpoint. Allowed methods: {endpoint.get_methods()}",
                params=params,
                url=url,
            )

        # Parse extra headers if provided
        extra_headers = None
        if extra_headers_str:
            try:
                import json

                extra_headers = json.loads(extra_headers_str)
            except Exception as e:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Invalid headers JSON: {e}",
                    params=params,
                    url=url,
                )

        # Build headers
        headers = self._toolset.build_headers(endpoint, extra_headers)

        # Get auth
        basic_auth = self._toolset.get_basic_auth(endpoint)

        # Make request
        try:
            if method == "GET":
                response = requests.get(
                    url,
                    headers=headers,
                    auth=basic_auth,
                    timeout=self._toolset.http_config.timeout_seconds,
                    verify=self._toolset.http_config.verify_ssl,
                )
            else:  # POST
                response = requests.post(
                    url,
                    headers=headers,
                    auth=basic_auth,
                    data=body,
                    timeout=self._toolset.http_config.timeout_seconds,
                    verify=self._toolset.http_config.verify_ssl,
                )

            # Return raw response (status + body)
            try:
                data = response.json()
            except Exception:
                data = response.text

            result = StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS
                if response.ok
                else StructuredToolResultStatus.ERROR,
                data={"status_code": response.status_code, "body": data},
                params=params,
                url=url,
            )

            # Apply JSON filtering from mixin
            return self.filter_result(result, params)

        except requests.exceptions.Timeout:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Request timed out after {self._toolset.http_config.timeout_seconds}s",
                params=params,
                url=url,
            )
        except requests.exceptions.ConnectionError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Connection error: {e}",
                params=params,
                url=url,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Request failed: {e}",
                params=params,
                url=url,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        url = params.get("url", "unknown")
        method = params.get("method", "GET").upper()
        # Truncate URL for display
        if len(url) > 50:
            url = url[:47] + "..."
        return f"HTTP {method} {url}"
