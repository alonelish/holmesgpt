"""
Tests that environment variable values expanded in toolset commands
are not leaked in invocation strings or error messages sent to the LLM.
"""

import os
from unittest.mock import patch

from holmes.core.tools import YAMLTool
from tests.conftest import create_mock_tool_invoke_context


class TestEnvVarRedactionInCommand:
    """Verify that __invoke_command does not expose env var secrets in invocation/error."""

    def test_successful_command_does_not_leak_env_var(self):
        secret = "ghp_super_secret_token_12345"
        tool = YAMLTool(
            name="test_tool",
            description="test",
            command='echo "using ${TEST_SECRET_TOKEN}"',
        )
        context = create_mock_tool_invoke_context()

        with patch.dict(os.environ, {"TEST_SECRET_TOKEN": secret}):
            result = tool.invoke({}, context)

        assert secret not in (result.invocation or "")
        assert "${TEST_SECRET_TOKEN}" in (result.invocation or "")

    def test_failed_command_does_not_leak_env_var_in_error(self):
        secret = "sk-secret-api-key-99999"
        tool = YAMLTool(
            name="test_tool",
            description="test",
            command='curl -H "Authorization: token ${TEST_SECRET_TOKEN}" http://localhost:99999/nonexistent',
        )
        context = create_mock_tool_invoke_context()

        with patch.dict(os.environ, {"TEST_SECRET_TOKEN": secret}):
            result = tool.invoke({}, context)

        # The command will fail (connection refused), producing an error
        assert result.error is not None
        assert secret not in result.error
        assert secret not in (result.invocation or "")
        assert "${TEST_SECRET_TOKEN}" in result.error

    def test_command_with_jinja_params_and_env_var(self):
        secret = "my_secret_api_key"
        tool = YAMLTool(
            name="test_tool",
            description="test",
            command='echo "${TEST_SECRET_TOKEN} {{ name }}"',
        )
        context = create_mock_tool_invoke_context()

        with patch.dict(os.environ, {"TEST_SECRET_TOKEN": secret}):
            result = tool.invoke({"name": "world"}, context)

        assert secret not in (result.invocation or "")
        # Jinja params should still be rendered
        assert "world" in (result.invocation or "")
        assert "${TEST_SECRET_TOKEN}" in (result.invocation or "")


class TestEnvVarRedactionInScript:
    """Verify that __invoke_script does not expose env var secrets in invocation/error."""

    def test_successful_script_does_not_leak_env_var(self):
        secret = "script_secret_value_abc"
        tool = YAMLTool(
            name="test_tool",
            description="test",
            script='#!/bin/bash\necho "token is ${TEST_SECRET_TOKEN}"',
        )
        context = create_mock_tool_invoke_context()

        with patch.dict(os.environ, {"TEST_SECRET_TOKEN": secret}):
            result = tool.invoke({}, context)

        assert secret not in (result.invocation or "")
        assert "${TEST_SECRET_TOKEN}" in (result.invocation or "")

    def test_failed_script_does_not_leak_env_var_in_error(self):
        secret = "script_secret_key_xyz"
        tool = YAMLTool(
            name="test_tool",
            description="test",
            script='#!/bin/bash\nexit 1',
        )
        context = create_mock_tool_invoke_context()

        with patch.dict(os.environ, {"TEST_SECRET_TOKEN": secret}):
            result = tool.invoke({}, context)

        assert result.error is not None
        assert secret not in result.error
        assert secret not in (result.invocation or "")
