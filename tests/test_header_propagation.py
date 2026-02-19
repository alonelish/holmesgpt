"""Tests for HTTP header propagation across toolset types.

Verifies that extra_headers configured on toolsets are rendered with
request_context and propagated to:
1. Shared header rendering utility
2. Toolset base class (render_extra_headers)
3. HTTP toolset (merged into outgoing requests)
4. YAML toolset (exposed as environment variables)
5. MCP toolset (merged with config-level headers)
6. ToolInvokeContext (pre-rendered headers)
"""

import os
from typing import Any, Dict, Optional, Tuple
from unittest.mock import Mock, patch

import pytest

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    YAMLTool,
    YAMLToolset,
)
from holmes.utils.header_rendering import (
    CaseInsensitiveDict,
    render_template_headers,
)


# ---------------------------------------------------------------------------
# Shared utility tests
# ---------------------------------------------------------------------------

class TestCaseInsensitiveDict:
    def test_case_insensitive_lookup(self):
        d = CaseInsensitiveDict({"X-Tenant-Id": "abc"})
        assert d["x-tenant-id"] == "abc"
        assert d["X-TENANT-ID"] == "abc"
        assert d["X-Tenant-Id"] == "abc"

    def test_missing_key_raises(self):
        d = CaseInsensitiveDict({"Foo": "bar"})
        with pytest.raises(KeyError):
            _ = d["Missing"]


class TestRenderTemplateHeaders:
    def test_static_value(self):
        result = render_template_headers({"X-Static": "hello"})
        assert result == {"X-Static": "hello"}

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("TEST_HEADER_VAR", "from-env")
        result = render_template_headers(
            {"X-Env": "{{ env.TEST_HEADER_VAR }}"}
        )
        assert result == {"X-Env": "from-env"}

    def test_request_context_header(self):
        ctx = {"headers": {"X-Tenant": "t-123"}}
        result = render_template_headers(
            {"X-Forwarded-Tenant": "{{ request_context.headers['X-Tenant'] }}"},
            request_context=ctx,
        )
        assert result == {"X-Forwarded-Tenant": "t-123"}

    def test_case_insensitive_request_context(self):
        ctx = {"headers": {"X-Token": "secret"}}
        result = render_template_headers(
            {"Auth": "{{ request_context.headers['x-token'] }}"},
            request_context=ctx,
        )
        assert result == {"Auth": "secret"}

    def test_missing_header_renders_empty(self):
        ctx = {"headers": {}}
        result = render_template_headers(
            {"X-Missing": "{{ request_context.headers['X-Nope'] }}"},
            request_context=ctx,
        )
        # Jinja2 KeyError on CaseInsensitiveDict - should be handled gracefully
        # The header is skipped with a warning
        assert "X-Missing" not in result or result["X-Missing"] == ""

    def test_no_request_context(self):
        result = render_template_headers(
            {"X-Static": "val"},
            request_context=None,
        )
        assert result == {"X-Static": "val"}

    def test_mixed_templates(self, monkeypatch):
        monkeypatch.setenv("API_SECRET", "s3cr3t")
        ctx = {"headers": {"X-Org": "org-42"}}
        result = render_template_headers(
            {
                "Authorization": "Bearer {{ env.API_SECRET }}",
                "X-Org-Id": "{{ request_context.headers['X-Org'] }}",
                "X-Version": "v1",
            },
            request_context=ctx,
        )
        assert result["Authorization"] == "Bearer s3cr3t"
        assert result["X-Org-Id"] == "org-42"
        assert result["X-Version"] == "v1"


# ---------------------------------------------------------------------------
# Toolset base class tests
# ---------------------------------------------------------------------------

class TestToolsetExtraHeaders:
    def test_render_extra_headers_returns_empty_when_none(self):
        ts = YAMLToolset(
            name="test",
            description="test",
            tools=[],
        )
        assert ts.render_extra_headers() == {}

    def test_render_extra_headers_static(self):
        ts = YAMLToolset(
            name="test",
            description="test",
            tools=[],
            extra_headers={"X-Custom": "static-value"},
        )
        assert ts.render_extra_headers() == {"X-Custom": "static-value"}

    def test_render_extra_headers_with_request_context(self):
        ts = YAMLToolset(
            name="test",
            description="test",
            tools=[],
            extra_headers={
                "X-Tenant": "{{ request_context.headers['X-Tenant-Id'] }}"
            },
        )
        ctx = {"headers": {"X-Tenant-Id": "tenant-abc"}}
        result = ts.render_extra_headers(ctx)
        assert result == {"X-Tenant": "tenant-abc"}

    def test_render_extra_headers_with_env(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "tok-123")
        ts = YAMLToolset(
            name="test",
            description="test",
            tools=[],
            extra_headers={"Authorization": "Bearer {{ env.MY_TOKEN }}"},
        )
        result = ts.render_extra_headers()
        assert result == {"Authorization": "Bearer tok-123"}


# ---------------------------------------------------------------------------
# ToolInvokeContext tests
# ---------------------------------------------------------------------------

class TestToolInvokeContextHeaders:
    def test_rendered_extra_headers_default_empty(self):
        ctx = ToolInvokeContext.model_construct(
            tool_number=1,
            user_approved=False,
            llm=Mock(),
            max_token_count=1000,
            tool_call_id="call-1",
            tool_name="test_tool",
        )
        assert ctx.rendered_extra_headers == {}

    def test_rendered_extra_headers_set(self):
        ctx = ToolInvokeContext.model_construct(
            tool_number=1,
            user_approved=False,
            llm=Mock(),
            max_token_count=1000,
            tool_call_id="call-1",
            tool_name="test_tool",
            rendered_extra_headers={"X-Foo": "bar"},
        )
        assert ctx.rendered_extra_headers == {"X-Foo": "bar"}

    def test_model_dump_redacts_rendered_extra_headers(self):
        ctx = ToolInvokeContext.model_construct(
            tool_number=1,
            user_approved=False,
            llm=Mock(),
            max_token_count=1000,
            tool_call_id="call-1",
            tool_name="test_tool",
            rendered_extra_headers={"X-Secret": "sensitive-value"},
            request_context={"headers": {"H1": "v1"}},
        )
        dumped = ctx.model_dump()
        assert dumped["rendered_extra_headers"]["X-Secret"] == "***REDACTED***"
        assert dumped["request_context"]["headers"] == "***REDACTED***"


# ---------------------------------------------------------------------------
# YAML tool environment variable propagation tests
# ---------------------------------------------------------------------------

class TestYAMLToolHeaderEnvVars:
    def test_build_header_env_vars_basic(self):
        env_vars = YAMLTool._build_header_env_vars(
            {"X-Custom-Token": "abc123", "Authorization": "Bearer xyz"}
        )
        assert env_vars == {
            "HOLMES_HEADER_X_CUSTOM_TOKEN": "abc123",
            "HOLMES_HEADER_AUTHORIZATION": "Bearer xyz",
        }

    def test_build_header_env_vars_empty(self):
        assert YAMLTool._build_header_env_vars({}) == {}

    def test_build_header_env_vars_special_chars(self):
        env_vars = YAMLTool._build_header_env_vars({"X.Dotted.Header": "val"})
        assert "HOLMES_HEADER_X_DOTTED_HEADER" in env_vars
        assert env_vars["HOLMES_HEADER_X_DOTTED_HEADER"] == "val"

    def test_yaml_tool_command_with_header_env_var(self):
        """Verify that rendered extra_headers are available as env vars in bash commands."""
        tool = YAMLTool(
            name="test_echo",
            description="Echo a header value",
            command='echo "$HOLMES_HEADER_X_TOKEN"',
        )
        context = ToolInvokeContext.model_construct(
            tool_number=1,
            user_approved=False,
            llm=Mock(),
            max_token_count=1000,
            tool_call_id="call-1",
            tool_name="test_echo",
            rendered_extra_headers={"X-Token": "my-secret-token"},
        )
        result = tool._invoke({}, context)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "my-secret-token"

    def test_yaml_tool_script_with_header_env_var(self):
        """Verify that rendered extra_headers are available as env vars in bash scripts."""
        tool = YAMLTool(
            name="test_script",
            description="Script using a header",
            script='#!/bin/bash\necho "$HOLMES_HEADER_AUTHORIZATION"',
        )
        context = ToolInvokeContext.model_construct(
            tool_number=1,
            user_approved=False,
            llm=Mock(),
            max_token_count=1000,
            tool_call_id="call-1",
            tool_name="test_script",
            rendered_extra_headers={"Authorization": "Bearer tok-456"},
        )
        result = tool._invoke({}, context)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "Bearer tok-456"

    def test_yaml_tool_no_extra_headers(self):
        """Verify YAML tools still work when no extra_headers are configured."""
        tool = YAMLTool(
            name="test_echo",
            description="Simple echo",
            command="echo hello",
        )
        context = ToolInvokeContext.model_construct(
            tool_number=1,
            user_approved=False,
            llm=Mock(),
            max_token_count=1000,
            tool_call_id="call-1",
            tool_name="test_echo",
            rendered_extra_headers={},
        )
        result = tool._invoke({}, context)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "hello"


# ---------------------------------------------------------------------------
# HTTP toolset header propagation tests
# ---------------------------------------------------------------------------

class TestHttpToolsetHeaderPropagation:
    @patch("holmes.plugins.toolsets.http.http_toolset.requests.request")
    def test_extra_headers_merged_into_request(self, mock_request):
        """Verify that toolset-level extra_headers are merged into HTTP requests."""
        from holmes.plugins.toolsets.http.http_toolset import HttpRequest, HttpToolset

        # Create an HTTP toolset with extra_headers
        toolset = HttpToolset(
            name="test_http",
            enabled=True,
            extra_headers={
                "X-Custom": "static-val",
            },
            config={
                "endpoints": [
                    {"hosts": ["api.example.com"], "methods": ["GET"]}
                ]
            },
        )
        ok, _ = toolset.prerequisites_callable({
            "endpoints": [
                {"hosts": ["api.example.com"], "methods": ["GET"]}
            ]
        })
        assert ok

        mock_response = Mock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "test"}
        mock_request.return_value = mock_response

        tool = toolset.tools[0]
        ctx = Mock(spec=ToolInvokeContext)
        ctx.rendered_extra_headers = {"X-Custom": "static-val"}

        result = tool._invoke(
            {"url": "https://api.example.com/test"},
            ctx,
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        # Verify the custom header was included in the request
        call_kwargs = mock_request.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        assert headers["X-Custom"] == "static-val"

    @patch("holmes.plugins.toolsets.http.http_toolset.requests.request")
    def test_extra_headers_override_defaults(self, mock_request):
        """Verify that extra_headers override default headers."""
        from holmes.plugins.toolsets.http.http_toolset import HttpRequest, HttpToolset

        toolset = HttpToolset(
            name="test_http",
            enabled=True,
            config={
                "endpoints": [
                    {"hosts": ["api.example.com"], "methods": ["GET"]}
                ],
                "default_headers": {"X-Default": "original"},
            },
        )
        ok, _ = toolset.prerequisites_callable({
            "endpoints": [
                {"hosts": ["api.example.com"], "methods": ["GET"]}
            ],
            "default_headers": {"X-Default": "original"},
        })
        assert ok

        mock_response = Mock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_request.return_value = mock_response

        tool = toolset.tools[0]
        ctx = Mock(spec=ToolInvokeContext)
        ctx.rendered_extra_headers = {"X-Default": "overridden"}

        result = tool._invoke(
            {"url": "https://api.example.com/test"},
            ctx,
        )

        call_kwargs = mock_request.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        assert headers["X-Default"] == "overridden"


# ---------------------------------------------------------------------------
# MCP toolset-level extra_headers merge tests
# ---------------------------------------------------------------------------

class TestMCPToolsetLevelExtraHeaders:
    def test_toolset_level_extra_headers_merged(self):
        """Verify that toolset-level extra_headers are merged with MCP config-level ones."""
        from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset

        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
            extra_headers={"X-Toolset-Level": "from-toolset"},
            config={
                "url": "http://localhost:1234",
                "extra_headers": {"X-Config-Level": "from-config"},
            },
        )
        mcp_toolset.prerequisites_callable(config=mcp_toolset.config)
        rendered = mcp_toolset._render_headers(None)

        assert rendered is not None
        assert rendered["X-Toolset-Level"] == "from-toolset"
        assert rendered["X-Config-Level"] == "from-config"

    def test_config_level_overrides_toolset_level(self):
        """Config-level extra_headers should take precedence over toolset-level."""
        from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset

        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
            extra_headers={"X-Shared": "toolset-value"},
            config={
                "url": "http://localhost:1234",
                "extra_headers": {"X-Shared": "config-value"},
            },
        )
        mcp_toolset.prerequisites_callable(config=mcp_toolset.config)
        rendered = mcp_toolset._render_headers(None)

        assert rendered is not None
        assert rendered["X-Shared"] == "config-value"

    def test_toolset_level_extra_headers_with_request_context(self):
        """Toolset-level extra_headers should render with request_context."""
        from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset

        mcp_toolset = RemoteMCPToolset(
            name="test_mcp",
            description="Test toolset",
            extra_headers={
                "X-Tenant": "{{ request_context.headers['X-Tenant-Id'] }}"
            },
            config={
                "url": "http://localhost:1234",
            },
        )
        mcp_toolset.prerequisites_callable(config=mcp_toolset.config)
        ctx = {"headers": {"X-Tenant-Id": "tenant-from-request"}}
        rendered = mcp_toolset._render_headers(ctx)

        assert rendered is not None
        assert rendered["X-Tenant"] == "tenant-from-request"
