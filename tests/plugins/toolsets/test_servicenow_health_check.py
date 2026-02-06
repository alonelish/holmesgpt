"""Tests for ServiceNow toolset two-tier health check.

Verifies that the health check distinguishes between:
1. Invalid API key (authentication failure)
2. Valid API key but missing permissions for sys_db_object table
3. Both checks passing (full access)
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from holmes.plugins.toolsets.servicenow_tables.servicenow_tables import (
    ServiceNowTablesConfig,
    ServiceNowTablesToolset,
)


@pytest.fixture
def toolset():
    ts = ServiceNowTablesToolset()
    ts.config = ServiceNowTablesConfig(
        api_key="test-key",
        api_url="https://test.service-now.com",
    )
    return ts


def _make_http_error(status_code: int, text: str = "") -> requests.exceptions.HTTPError:
    """Create an HTTPError with a mock response."""
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    error = requests.exceptions.HTTPError(response=response)
    return error


class TestAuthenticationCheck:
    """Tests for the first tier: authentication verification via sys_user."""

    def test_auth_success(self, toolset):
        with patch.object(toolset, "_make_api_request") as mock_req:
            mock_req.return_value = ({"result": [{"sys_id": "abc"}]}, {})
            ok, msg = toolset._check_authentication()

        assert ok is True
        assert "authentication successful" in msg.lower()
        mock_req.assert_called_once_with(
            endpoint="api/now/v2/table/sys_user",
            query_params={"sysparm_limit": 1, "sysparm_fields": "sys_id"},
            timeout=10,
        )

    def test_auth_401_invalid_key(self, toolset):
        with patch.object(toolset, "_make_api_request") as mock_req:
            mock_req.side_effect = _make_http_error(401)
            ok, msg = toolset._check_authentication()

        assert ok is False
        assert "authentication failed" in msg.lower()
        assert "check your API key" in msg

    def test_auth_403_rejected_key(self, toolset):
        with patch.object(toolset, "_make_api_request") as mock_req:
            mock_req.side_effect = _make_http_error(403)
            ok, msg = toolset._check_authentication()

        assert ok is False
        assert "authentication failed" in msg.lower()
        assert "api_key_header" in msg
        assert "x-sn-apikey" in msg

    def test_auth_403_custom_header_shown(self):
        ts = ServiceNowTablesToolset()
        ts.config = ServiceNowTablesConfig(
            api_key="test-key",
            api_url="https://test.service-now.com",
            api_key_header="Authorization",
        )
        with patch.object(ts, "_make_api_request") as mock_req:
            mock_req.side_effect = _make_http_error(403)
            ok, msg = ts._check_authentication()

        assert ok is False
        assert "Authorization" in msg

    def test_auth_500_server_error(self, toolset):
        with patch.object(toolset, "_make_api_request") as mock_req:
            mock_req.side_effect = _make_http_error(500, "Internal Server Error")
            ok, msg = toolset._check_authentication()

        assert ok is False
        assert "500" in msg

    def test_auth_connection_error(self, toolset):
        with patch.object(toolset, "_make_api_request") as mock_req:
            mock_req.side_effect = requests.exceptions.ConnectionError("Connection refused")
            ok, msg = toolset._check_authentication()

        assert ok is False
        assert "Failed to connect" in msg
        assert "test.service-now.com" in msg

    def test_auth_timeout(self, toolset):
        with patch.object(toolset, "_make_api_request") as mock_req:
            mock_req.side_effect = requests.exceptions.Timeout("Request timed out")
            ok, msg = toolset._check_authentication()

        assert ok is False
        assert "timed out" in msg.lower()


class TestTableDiscoveryAccessCheck:
    """Tests for the second tier: sys_db_object table access verification."""

    def test_sys_db_object_accessible(self, toolset):
        with patch.object(toolset, "_make_api_request") as mock_req:
            mock_req.return_value = ({"result": [{"sys_id": "abc"}]}, {})
            ok, msg = toolset._check_table_discovery_access()

        assert ok is True
        assert "valid and API is accessible" in msg
        mock_req.assert_called_once_with(
            endpoint="api/now/v2/table/sys_db_object",
            query_params={"sysparm_limit": 1, "sysparm_fields": "sys_id"},
            timeout=10,
        )

    def test_sys_db_object_403_still_enabled(self, toolset):
        """Toolset should still be enabled when sys_db_object returns 403."""
        with patch.object(toolset, "_make_api_request") as mock_req:
            mock_req.side_effect = _make_http_error(403)
            ok, msg = toolset._check_table_discovery_access()

        assert ok is True
        assert "authentication is valid" in msg.lower()
        assert "does not have permission" in msg
        assert "sys_db_object" in msg
        assert "still query tables" in msg

    def test_sys_db_object_401_still_enabled(self, toolset):
        """Toolset should still be enabled when sys_db_object returns 401."""
        with patch.object(toolset, "_make_api_request") as mock_req:
            mock_req.side_effect = _make_http_error(401)
            ok, msg = toolset._check_table_discovery_access()

        assert ok is True
        assert "authentication is valid" in msg.lower()
        assert "still query tables" in msg

    def test_sys_db_object_500_still_enabled(self, toolset):
        """Toolset should still be enabled even on 500 errors for sys_db_object."""
        with patch.object(toolset, "_make_api_request") as mock_req:
            mock_req.side_effect = _make_http_error(500, "Server Error")
            ok, msg = toolset._check_table_discovery_access()

        assert ok is True
        assert "authentication is valid" in msg.lower()
        assert "500" in msg

    def test_sys_db_object_exception_still_enabled(self, toolset):
        """Toolset should still be enabled even on unexpected exceptions."""
        with patch.object(toolset, "_make_api_request") as mock_req:
            mock_req.side_effect = Exception("Unexpected error")
            ok, msg = toolset._check_table_discovery_access()

        assert ok is True
        assert "authentication is valid" in msg.lower()
        assert "toolset is enabled" in msg


class TestHealthCheckIntegration:
    """Tests for the combined two-tier health check flow."""

    def test_both_checks_pass(self, toolset):
        with patch.object(toolset, "_make_api_request") as mock_req:
            mock_req.return_value = ({"result": [{"sys_id": "abc"}]}, {})
            ok, msg = toolset._perform_health_check()

        assert ok is True
        assert "valid and API is accessible" in msg
        assert mock_req.call_count == 2

    def test_auth_fails_skips_second_check(self, toolset):
        """When auth fails, sys_db_object should not be checked."""
        with patch.object(toolset, "_make_api_request") as mock_req:
            mock_req.side_effect = _make_http_error(401)
            ok, msg = toolset._perform_health_check()

        assert ok is False
        assert "authentication failed" in msg.lower()
        # Only one call should be made (sys_user), not two
        assert mock_req.call_count == 1

    def test_auth_passes_but_sys_db_object_forbidden(self, toolset):
        """Auth passes, sys_db_object forbidden -> toolset still enabled."""
        call_count = 0

        def side_effect(endpoint, query_params=None, timeout=30):
            nonlocal call_count
            call_count += 1
            if "sys_user" in endpoint:
                return ({"result": [{"sys_id": "abc"}]}, {})
            elif "sys_db_object" in endpoint:
                raise _make_http_error(403)
            raise AssertionError(f"Unexpected endpoint: {endpoint}")

        with patch.object(toolset, "_make_api_request", side_effect=side_effect):
            ok, msg = toolset._perform_health_check()

        assert ok is True
        assert "does not have permission" in msg
        assert "still query tables" in msg
        assert call_count == 2

    def test_prerequisites_callable_full_flow(self, toolset):
        """Test the full prerequisites_callable entry point."""
        config = {
            "api_key": "test-key",
            "api_url": "https://test.service-now.com",
        }

        with patch.object(toolset, "_make_api_request") as mock_req:
            mock_req.return_value = ({"result": [{"sys_id": "abc"}]}, {})
            ok, msg = toolset.prerequisites_callable(config)

        assert ok is True

    def test_prerequisites_callable_invalid_config(self, toolset):
        """Test that missing required fields are caught."""
        config = {"api_key": "test-key"}  # missing api_url

        ok, msg = toolset.prerequisites_callable(config)

        assert ok is False
        assert "Failed to validate" in msg
