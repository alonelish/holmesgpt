"""
Tests for AI-Alertmanager API endpoints.
"""

import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock

from app.main import app, storage
from app.models import PostableAlert


@pytest.fixture
def client():
    """Create a test client."""
    # Clear storage before each test
    storage.clear()
    return TestClient(app)


@pytest.fixture
def sample_alert():
    """Create a sample alert for testing."""
    return PostableAlert(
        labels={
            "alertname": "HighMemoryUsage",
            "severity": "warning",
            "namespace": "production",
        },
        annotations={
            "summary": "High memory usage detected",
            "description": "Memory usage is above 90%",
        },
        startsAt=datetime.utcnow(),
        generatorURL="http://prometheus:9090/graph?g0.expr=...",
    )


def test_health_check(client):
    """Test health check endpoint."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_readiness_check(client):
    """Test readiness check endpoint."""
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_get_status(client):
    """Test status endpoint."""
    response = client.get("/api/v2/status")
    assert response.status_code == 200
    data = response.json()
    assert "cluster" in data
    assert "versionInfo" in data


def test_post_alerts_v2(client, sample_alert):
    """Test posting alerts to v2 API."""
    response = client.post(
        "/api/v2/alerts",
        json=[sample_alert.model_dump(mode="json")]
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Verify alert was stored
    alerts = storage.get_all_alerts()
    assert len(alerts) == 1
    assert alerts[0].labels["alertname"] == "HighMemoryUsage"


def test_post_alerts_v1(client, sample_alert):
    """Test posting alerts to v1 API (backwards compatibility)."""
    response = client.post(
        "/api/v1/alerts",
        json=[sample_alert.model_dump(mode="json")]
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_get_alerts_empty(client):
    """Test getting alerts when none exist."""
    response = client.get("/api/v2/alerts")
    assert response.status_code == 200
    assert response.json() == []


def test_get_alerts_with_data(client, sample_alert):
    """Test getting alerts after posting."""
    # Post alert first
    client.post("/api/v2/alerts", json=[sample_alert.model_dump(mode="json")])

    # Get alerts
    response = client.get("/api/v2/alerts")
    assert response.status_code == 200
    alerts = response.json()
    assert len(alerts) == 1
    assert alerts[0]["labels"]["alertname"] == "HighMemoryUsage"


def test_get_alerts_with_filter(client):
    """Test getting alerts with label filters."""
    # Post multiple alerts
    alert1 = PostableAlert(
        labels={"alertname": "Alert1", "severity": "warning"},
        annotations={},
        startsAt=datetime.utcnow(),
    )
    alert2 = PostableAlert(
        labels={"alertname": "Alert2", "severity": "critical"},
        annotations={},
        startsAt=datetime.utcnow(),
    )

    client.post("/api/v2/alerts", json=[alert1.model_dump(mode="json")])
    client.post("/api/v2/alerts", json=[alert2.model_dump(mode="json")])

    # Filter by severity
    response = client.get("/api/v2/alerts?filter=severity=critical")
    assert response.status_code == 200
    alerts = response.json()
    assert len(alerts) == 1
    assert alerts[0]["labels"]["alertname"] == "Alert2"


def test_get_alert_groups(client, sample_alert):
    """Test getting alert groups."""
    # Post alert first
    client.post("/api/v2/alerts", json=[sample_alert.model_dump(mode="json")])

    # Get groups
    response = client.get("/api/v2/alerts/groups")
    assert response.status_code == 200
    groups = response.json()
    assert len(groups) == 1
    assert len(groups[0]["alerts"]) == 1


def test_get_receivers(client):
    """Test getting receivers."""
    response = client.get("/api/v2/receivers")
    assert response.status_code == 200
    receivers = response.json()
    assert len(receivers) == 1
    assert receivers[0]["name"] == "default"


def test_get_silences_empty(client):
    """Test getting silences (not implemented yet)."""
    response = client.get("/api/v2/silences")
    assert response.status_code == 200
    assert response.json() == []


def test_create_silence_not_implemented(client):
    """Test creating silence (not implemented yet)."""
    response = client.post("/api/v2/silences", json={})
    assert response.status_code == 501


def test_fingerprint_consistency(client, sample_alert):
    """Test that same alert produces same fingerprint."""
    # Post alert twice
    client.post("/api/v2/alerts", json=[sample_alert.model_dump(mode="json")])
    client.post("/api/v2/alerts", json=[sample_alert.model_dump(mode="json")])

    # Should only have one alert (deduplicated)
    alerts = storage.get_all_alerts()
    assert len(alerts) == 1


def test_get_investigation_not_found(client):
    """Test getting investigation for non-existent alert."""
    response = client.get("/api/v2/investigations/nonexistent")
    assert response.status_code == 404


def test_trigger_investigation_not_found(client):
    """Test triggering investigation for non-existent alert."""
    response = client.post("/api/v2/investigate/nonexistent")
    assert response.status_code == 404


@patch("app.main.holmes_client")
def test_investigation_integration(mock_holmes, client, sample_alert):
    """Test investigation integration with HolmesGPT."""
    # Mock HolmesGPT client
    mock_holmes.investigate_alert = AsyncMock(return_value={
        "analysis": "Root cause: High memory consumption due to memory leak",
        "tool_calls": [],
    })
    mock_holmes.format_investigation_for_label = MagicMock(
        return_value="Root cause: High memory consumption due to memory leak"
    )
    mock_holmes.extract_root_cause = MagicMock(
        return_value="High memory consumption due to memory leak"
    )

    # Post alert
    response = client.post(
        "/api/v2/alerts",
        json=[sample_alert.model_dump(mode="json")]
    )
    assert response.status_code == 200

    # Note: In real testing, we would wait for the background task
    # For unit tests, we're just verifying the endpoint works


def test_get_all_investigations_empty(client):
    """Test getting all investigations when none exist."""
    response = client.get("/api/v2/investigations")
    assert response.status_code == 200
    assert response.json() == []
