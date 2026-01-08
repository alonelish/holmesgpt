"""
Tests for HolmesGPT client integration.
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.holmes_client import HolmesClient


@pytest.fixture
def holmes_client():
    """Create a HolmesGPT client for testing."""
    return HolmesClient(base_url="http://localhost:8080", timeout=60)


@pytest.mark.asyncio
async def test_investigate_alert(holmes_client):
    """Test alert investigation."""
    # Mock the HTTP client
    with patch.object(holmes_client.client, "post") as mock_post:
        # Use MagicMock for response since json() is synchronous in httpx
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "analysis": "Root cause: Memory leak in application",
            "tool_calls": [],
            "num_llm_calls": 3,
        }
        mock_response.raise_for_status = MagicMock()
        # post() is async, so mock_post needs to return a coroutine
        async def async_post(*args, **kwargs):
            return mock_response
        mock_post.side_effect = async_post

        result = await holmes_client.investigate_alert(
            alert_name="HighMemoryUsage",
            alert_labels={
                "alertname": "HighMemoryUsage",
                "severity": "warning",
                "namespace": "production",
            },
            alert_annotations={
                "summary": "High memory usage detected",
                "description": "Memory usage is above 90%",
            },
            starts_at=datetime.utcnow(),
            generator_url="http://prometheus:9090/graph",
        )

        assert result["analysis"] == "Root cause: Memory leak in application"
        assert mock_post.called


@pytest.mark.asyncio
async def test_investigate_alert_with_error(holmes_client):
    """Test alert investigation with error."""
    # Mock the HTTP client to raise an error
    with patch.object(holmes_client.client, "post") as mock_post:
        mock_post.side_effect = Exception("Connection error")

        with pytest.raises(Exception, match="Connection error"):
            await holmes_client.investigate_alert(
                alert_name="TestAlert",
                alert_labels={"alertname": "TestAlert"},
                alert_annotations={},
                starts_at=datetime.utcnow(),
            )


def test_format_investigation_for_label(holmes_client):
    """Test formatting investigation result for label."""
    investigation_result = {
        "analysis": "Root cause: Memory leak in application. This is causing high memory usage.",
    }

    formatted = holmes_client.format_investigation_for_label(investigation_result)

    assert formatted == "Root cause: Memory leak in application. This is causing high memory usage."


def test_format_investigation_for_label_long_text(holmes_client):
    """Test formatting long investigation result for label."""
    investigation_result = {
        "analysis": "A" * 300,  # 300 character string
    }

    formatted = holmes_client.format_investigation_for_label(investigation_result)

    # Should be truncated to 200 chars
    assert len(formatted) == 200
    assert formatted.endswith("...")


def test_format_investigation_for_label_empty(holmes_client):
    """Test formatting empty investigation result."""
    investigation_result = {}

    formatted = holmes_client.format_investigation_for_label(investigation_result)

    assert formatted == "Investigation completed - no analysis available"


def test_extract_root_cause(holmes_client):
    """Test extracting root cause from investigation."""
    investigation_result = {
        "analysis": "The issue is caused by a memory leak. Root cause: Application is not releasing memory properly.",
    }

    root_cause = holmes_client.extract_root_cause(investigation_result)

    assert root_cause is not None
    assert "root cause:" in root_cause.lower()


def test_extract_root_cause_with_due_to(holmes_client):
    """Test extracting root cause with 'due to' keyword."""
    investigation_result = {
        "analysis": "The high memory usage is due to: excessive caching without eviction policy.",
    }

    root_cause = holmes_client.extract_root_cause(investigation_result)

    assert root_cause is not None
    assert "due to:" in root_cause.lower()


def test_extract_root_cause_not_found(holmes_client):
    """Test extracting root cause when not present."""
    investigation_result = {
        "analysis": "The investigation is ongoing.",
    }

    root_cause = holmes_client.extract_root_cause(investigation_result)

    assert root_cause is None


@pytest.mark.asyncio
async def test_close_client(holmes_client):
    """Test closing the client."""
    with patch.object(holmes_client.client, "aclose") as mock_close:
        await holmes_client.close()
        assert mock_close.called
