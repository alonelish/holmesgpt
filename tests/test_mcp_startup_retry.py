from unittest.mock import MagicMock, patch

import pytest

from holmes.core.tools import ToolsetStatusEnum, ToolsetType


def _make_toolset(name: str, toolset_type: ToolsetType, status: ToolsetStatusEnum):
    ts = MagicMock()
    ts.name = name
    ts.type = toolset_type
    ts.status = status
    return ts


class TestMCPStartupRetry:
    """Tests for _retry_failed_mcp_toolsets_at_startup in server.py."""

    @patch("server.time.sleep")
    @patch("server.config")
    @patch("server.holmes_sync_toolsets_status")
    @patch("server.MCP_STARTUP_RETRY_SCHEDULE", [5, 10])
    def test_no_retry_when_all_mcp_healthy(self, mock_sync, mock_config, mock_sleep):
        """If no MCP toolsets are failed, no retries happen."""
        healthy_toolset = _make_toolset("mcp-ok", ToolsetType.MCP, ToolsetStatusEnum.ENABLED)
        executor = MagicMock()
        executor.toolsets = [healthy_toolset]
        mock_config._server_tool_executor = executor

        from server import _retry_failed_mcp_toolsets_at_startup

        _retry_failed_mcp_toolsets_at_startup()

        mock_sleep.assert_not_called()
        mock_config.refresh_server_tool_executor.assert_not_called()

    @patch("server.time.sleep")
    @patch("server.dal")
    @patch("server.config")
    @patch("server.holmes_sync_toolsets_status")
    @patch("server.MCP_STARTUP_RETRY_SCHEDULE", [5, 10])
    def test_retries_until_mcp_recovers(self, mock_sync, mock_config, mock_dal, mock_sleep):
        """Retries and stops once the MCP toolset recovers."""
        mcp_toolset = _make_toolset("mcp-aws", ToolsetType.MCP, ToolsetStatusEnum.FAILED)
        executor = MagicMock()
        executor.toolsets = [mcp_toolset]
        mock_config._server_tool_executor = executor

        # After first refresh call, mark toolset as recovered
        def recover_on_refresh(dal):
            mcp_toolset.status = ToolsetStatusEnum.ENABLED
            return [("mcp-aws", "failed", "enabled")]

        mock_config.refresh_server_tool_executor.side_effect = recover_on_refresh

        from server import _retry_failed_mcp_toolsets_at_startup

        _retry_failed_mcp_toolsets_at_startup()

        # Should sleep once (5s), then succeed
        mock_sleep.assert_called_once_with(5)
        mock_config.refresh_server_tool_executor.assert_called_once()
        mock_sync.assert_called_once()

    @patch("server.time.sleep")
    @patch("server.dal")
    @patch("server.config")
    @patch("server.holmes_sync_toolsets_status")
    @patch("server.MCP_STARTUP_RETRY_SCHEDULE", [5, 10])
    def test_exhausts_all_retries_when_mcp_stays_failed(
        self, mock_sync, mock_config, mock_dal, mock_sleep
    ):
        """Goes through all retries when MCP never recovers."""
        mcp_toolset = _make_toolset("mcp-aws", ToolsetType.MCP, ToolsetStatusEnum.FAILED)
        executor = MagicMock()
        executor.toolsets = [mcp_toolset]
        mock_config._server_tool_executor = executor

        # Return no changes (still failed)
        mock_config.refresh_server_tool_executor.return_value = []

        from server import _retry_failed_mcp_toolsets_at_startup

        _retry_failed_mcp_toolsets_at_startup()

        # Should sleep for each entry in the schedule
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(5)
        mock_sleep.assert_any_call(10)
        assert mock_config.refresh_server_tool_executor.call_count == 2

    @patch("server.time.sleep")
    @patch("server.dal")
    @patch("server.config")
    @patch("server.holmes_sync_toolsets_status")
    @patch("server.MCP_STARTUP_RETRY_SCHEDULE", [5, 10, 20])
    def test_recovers_on_second_retry(self, mock_sync, mock_config, mock_dal, mock_sleep):
        """MCP recovers on the second retry attempt."""
        mcp_toolset = _make_toolset("mcp-aws", ToolsetType.MCP, ToolsetStatusEnum.FAILED)
        executor = MagicMock()
        executor.toolsets = [mcp_toolset]
        mock_config._server_tool_executor = executor

        call_count = 0

        def recover_on_second_call(dal):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                mcp_toolset.status = ToolsetStatusEnum.ENABLED
                return [("mcp-aws", "failed", "enabled")]
            return []

        mock_config.refresh_server_tool_executor.side_effect = recover_on_second_call

        from server import _retry_failed_mcp_toolsets_at_startup

        _retry_failed_mcp_toolsets_at_startup()

        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(5)
        mock_sleep.assert_any_call(10)
        assert mock_config.refresh_server_tool_executor.call_count == 2

    @patch("server.time.sleep")
    @patch("server.dal")
    @patch("server.config")
    @patch("server.holmes_sync_toolsets_status")
    @patch("server.MCP_STARTUP_RETRY_SCHEDULE", [5])
    def test_handles_refresh_exception(self, mock_sync, mock_config, mock_dal, mock_sleep):
        """Continues retrying even if refresh raises an exception."""
        mcp_toolset = _make_toolset("mcp-aws", ToolsetType.MCP, ToolsetStatusEnum.FAILED)
        executor = MagicMock()
        executor.toolsets = [mcp_toolset]
        mock_config._server_tool_executor = executor

        mock_config.refresh_server_tool_executor.side_effect = Exception("connection refused")

        from server import _retry_failed_mcp_toolsets_at_startup

        # Should not raise
        _retry_failed_mcp_toolsets_at_startup()

        mock_sleep.assert_called_once_with(5)

    @patch("server.time.sleep")
    @patch("server.config")
    @patch("server.holmes_sync_toolsets_status")
    @patch("server.MCP_STARTUP_RETRY_SCHEDULE", [5])
    def test_skips_non_mcp_failed_toolsets(self, mock_sync, mock_config, mock_sleep):
        """Only MCP toolsets trigger the startup retry."""
        non_mcp_toolset = _make_toolset("kubernetes", ToolsetType.BUILTIN, ToolsetStatusEnum.FAILED)
        executor = MagicMock()
        executor.toolsets = [non_mcp_toolset]
        mock_config._server_tool_executor = executor

        from server import _retry_failed_mcp_toolsets_at_startup

        _retry_failed_mcp_toolsets_at_startup()

        mock_sleep.assert_not_called()
        mock_config.refresh_server_tool_executor.assert_not_called()
