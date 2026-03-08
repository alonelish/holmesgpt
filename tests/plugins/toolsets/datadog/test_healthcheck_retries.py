"""Tests for Datadog healthcheck retry logic."""

from unittest.mock import MagicMock, patch

import requests
from requests.structures import CaseInsensitiveDict

from holmes.plugins.toolsets.datadog.datadog_api import (
    DataDogRequestError,
    perform_healthcheck_with_retries,
)


def _make_dd_error(status_code: int, response_text: str = "error") -> DataDogRequestError:
    return DataDogRequestError(
        payload={},
        status_code=status_code,
        response_text=response_text,
        response_headers=CaseInsensitiveDict({}),
    )


class TestHealthcheckRetries:
    """Test perform_healthcheck_with_retries retry logic."""

    @patch("holmes.plugins.toolsets.datadog.datadog_api.execute_datadog_http_request")
    def test_success_on_first_attempt(self, mock_execute):
        mock_execute.return_value = {"data": []}
        data, success, error = perform_healthcheck_with_retries(
            url="https://api.datadoghq.com/api/v2/logs/events/search",
            headers={},
            payload_or_params={},
            timeout=60,
            toolset_name="datadog/logs",
        )
        assert success is True
        assert error == ""
        assert mock_execute.call_count == 1

    @patch("holmes.plugins.toolsets.datadog.datadog_api.execute_datadog_http_request")
    def test_403_fails_immediately_no_retry(self, mock_execute):
        mock_execute.side_effect = _make_dd_error(403, "Logs Analytics API not configured")
        _, success, error = perform_healthcheck_with_retries(
            url="https://api.datadoghq.com/api/v2/logs/events/search",
            headers={},
            payload_or_params={},
            timeout=60,
            toolset_name="datadog/logs",
        )
        assert success is False
        assert "API key lacks required permissions" in error
        # Should NOT retry — only 1 call
        assert mock_execute.call_count == 1

    @patch("holmes.plugins.toolsets.datadog.datadog_api.execute_datadog_http_request")
    def test_401_fails_immediately_no_retry(self, mock_execute):
        mock_execute.side_effect = _make_dd_error(401, '{"errors":["Unauthorized"]}')
        _, success, error = perform_healthcheck_with_retries(
            url="https://api.datadoghq.com/api/v2/logs/events/search",
            headers={},
            payload_or_params={},
            timeout=60,
            toolset_name="datadog/logs",
        )
        assert success is False
        assert "401" in error
        assert mock_execute.call_count == 1

    @patch("holmes.plugins.toolsets.datadog.datadog_api.time.sleep")
    @patch("holmes.plugins.toolsets.datadog.datadog_api.execute_datadog_http_request")
    def test_408_retries_then_succeeds(self, mock_execute, mock_sleep):
        """408 timeout is transient — should retry and succeed on second attempt."""
        mock_execute.side_effect = [
            _make_dd_error(408, "Request Timeout"),
            {"data": []},  # Success on retry
        ]
        data, success, error = perform_healthcheck_with_retries(
            url="https://api.datadoghq.com/api/v2/logs/events/search",
            headers={},
            payload_or_params={},
            timeout=60,
            toolset_name="datadog/logs",
        )
        assert success is True
        assert error == ""
        assert mock_execute.call_count == 2
        mock_sleep.assert_called_once()

    @patch("holmes.plugins.toolsets.datadog.datadog_api.time.sleep")
    @patch("holmes.plugins.toolsets.datadog.datadog_api.execute_datadog_http_request")
    def test_408_retries_exhausted(self, mock_execute, mock_sleep):
        """408 timeout persists through all retries — should fail after max attempts."""
        mock_execute.side_effect = _make_dd_error(408, "Request Timeout")
        _, success, error = perform_healthcheck_with_retries(
            url="https://api.datadoghq.com/api/v2/logs/events/search",
            headers={},
            payload_or_params={},
            timeout=60,
            toolset_name="datadog/logs",
        )
        assert success is False
        assert "408" in error
        assert mock_execute.call_count == 3  # HEALTHCHECK_MAX_RETRIES

    @patch("holmes.plugins.toolsets.datadog.datadog_api.time.sleep")
    @patch("holmes.plugins.toolsets.datadog.datadog_api.execute_datadog_http_request")
    def test_500_retries_then_succeeds(self, mock_execute, mock_sleep):
        """500 server error is transient — should retry."""
        mock_execute.side_effect = [
            _make_dd_error(500, "Internal Server Error"),
            {"data": []},
        ]
        _, success, error = perform_healthcheck_with_retries(
            url="https://api.datadoghq.com/api/v2/logs/events/search",
            headers={},
            payload_or_params={},
            timeout=60,
            toolset_name="datadog/logs",
        )
        assert success is True
        assert mock_execute.call_count == 2

    @patch("holmes.plugins.toolsets.datadog.datadog_api.time.sleep")
    @patch("holmes.plugins.toolsets.datadog.datadog_api.execute_datadog_http_request")
    def test_connection_error_retries(self, mock_execute, mock_sleep):
        """Connection errors are transient — should retry."""
        mock_execute.side_effect = [
            requests.ConnectionError("Connection refused"),
            {"data": []},
        ]
        _, success, error = perform_healthcheck_with_retries(
            url="https://api.datadoghq.com/api/v2/logs/events/search",
            headers={},
            payload_or_params={},
            timeout=60,
            toolset_name="datadog/logs",
        )
        assert success is True
        assert mock_execute.call_count == 2

    @patch("holmes.plugins.toolsets.datadog.datadog_api.execute_datadog_http_request")
    def test_non_transient_exception_fails_immediately(self, mock_execute):
        """Non-transient exceptions (e.g. ValueError) should fail immediately."""
        mock_execute.side_effect = ValueError("bad config")
        _, success, error = perform_healthcheck_with_retries(
            url="https://api.datadoghq.com/api/v2/logs/events/search",
            headers={},
            payload_or_params={},
            timeout=60,
            toolset_name="datadog/logs",
        )
        assert success is False
        assert "bad config" in error
        assert mock_execute.call_count == 1
