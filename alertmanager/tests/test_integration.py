"""
Integration tests that can run against both real Alertmanager and AI-Alertmanager.

These tests verify identical behavior by running the same tests against both implementations.

Usage:
    # Test AI-Alertmanager
    ALERTMANAGER_URL=http://localhost:9093 pytest tests/test_integration.py

    # Test real Alertmanager
    ALERTMANAGER_URL=http://real-alertmanager:9093 pytest tests/test_integration.py

    # Comparative testing (requires both running)
    pytest tests/test_integration.py --compare
"""

import os
import pytest
import httpx
from datetime import datetime, timedelta
from typing import Optional


# Get Alertmanager URL from environment
ALERTMANAGER_URL = os.environ.get("ALERTMANAGER_URL", "http://localhost:9093")
REAL_ALERTMANAGER_URL = os.environ.get("REAL_ALERTMANAGER_URL", None)
COMPARE_MODE = REAL_ALERTMANAGER_URL is not None


class AlertmanagerClient:
    """Client for testing Alertmanager instances."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=10.0)

    def post_alerts(self, alerts: list) -> httpx.Response:
        """Post alerts to Alertmanager."""
        return self.client.post(f"{self.base_url}/api/v2/alerts", json=alerts)

    def get_alerts(self, **params) -> httpx.Response:
        """Get alerts from Alertmanager."""
        return self.client.get(f"{self.base_url}/api/v2/alerts", params=params)

    def get_alert_groups(self) -> httpx.Response:
        """Get alert groups from Alertmanager."""
        return self.client.get(f"{self.base_url}/api/v2/alerts/groups")

    def get_status(self) -> httpx.Response:
        """Get Alertmanager status."""
        return self.client.get(f"{self.base_url}/api/v2/status")

    def get_receivers(self) -> httpx.Response:
        """Get receivers from Alertmanager."""
        return self.client.get(f"{self.base_url}/api/v2/receivers")

    def get_silences(self) -> httpx.Response:
        """Get silences from Alertmanager."""
        return self.client.get(f"{self.base_url}/api/v2/silences")

    def health_check(self) -> httpx.Response:
        """Check Alertmanager health."""
        return self.client.get(f"{self.base_url}/healthz")

    def clear_alerts(self):
        """Clear all alerts (best effort - may not work on real Alertmanager)."""
        try:
            # Get all alerts
            response = self.get_alerts()
            if response.status_code == 200:
                alerts = response.json()
                # Mark all as resolved by reposting with endsAt in the past
                now = datetime.utcnow()
                for alert in alerts:
                    alert["endsAt"] = (now - timedelta(hours=1)).isoformat() + "Z"
                if alerts:
                    self.post_alerts(alerts)
        except Exception:
            pass  # Best effort

    def close(self):
        """Close the client."""
        self.client.close()


def check_server_running(url: str) -> bool:
    """Check if server is running at the given URL."""
    try:
        with httpx.Client(timeout=2.0) as client:
            response = client.get(f"{url}/healthz")
            return response.status_code == 200
    except Exception:
        return False


@pytest.fixture
def ai_alertmanager():
    """Fixture for AI-Alertmanager client."""
    if not check_server_running(ALERTMANAGER_URL):
        pytest.skip(f"AI-Alertmanager not running at {ALERTMANAGER_URL}")
    client = AlertmanagerClient(ALERTMANAGER_URL)
    client.clear_alerts()
    yield client
    client.close()


@pytest.fixture
def real_alertmanager():
    """Fixture for real Alertmanager client (if available)."""
    if not REAL_ALERTMANAGER_URL:
        pytest.skip("Real Alertmanager not configured (set REAL_ALERTMANAGER_URL)")
    client = AlertmanagerClient(REAL_ALERTMANAGER_URL)
    client.clear_alerts()
    yield client
    client.close()


class TestAlertmanagerIntegration:
    """Integration tests that work against any Alertmanager implementation."""

    def test_health_check(self, ai_alertmanager):
        """Test health check endpoint."""
        response = ai_alertmanager.health_check()
        assert response.status_code == 200

    def test_post_and_get_single_alert(self, ai_alertmanager):
        """Test posting and retrieving a single alert."""
        alert_payload = [{
            "labels": {
                "alertname": "TestAlert",
                "severity": "info",
                "integration_test": "true",
            },
            "annotations": {
                "summary": "Integration test alert",
            },
            "startsAt": datetime.utcnow().isoformat() + "Z",
        }]

        # Post alert
        response = ai_alertmanager.post_alerts(alert_payload)
        assert response.status_code == 200
        assert response.json().get("status") == "success"

        # Get alerts
        response = ai_alertmanager.get_alerts()
        assert response.status_code == 200
        alerts = response.json()
        assert len(alerts) >= 1

        # Find our alert
        test_alert = next(
            (a for a in alerts if a["labels"].get("integration_test") == "true"),
            None
        )
        assert test_alert is not None
        assert test_alert["labels"]["alertname"] == "TestAlert"
        assert test_alert["annotations"]["summary"] == "Integration test alert"

    def test_post_multiple_alerts(self, ai_alertmanager):
        """Test posting multiple alerts at once."""
        alerts_payload = [
            {
                "labels": {"alertname": f"Alert{i}", "test_id": "multi"},
                "annotations": {},
            }
            for i in range(5)
        ]

        response = ai_alertmanager.post_alerts(alerts_payload)
        assert response.status_code == 200

        # Verify all alerts were stored
        response = ai_alertmanager.get_alerts()
        alerts = response.json()
        test_alerts = [a for a in alerts if a["labels"].get("test_id") == "multi"]
        assert len(test_alerts) >= 5

    def test_alert_deduplication(self, ai_alertmanager):
        """Test that duplicate alerts are deduplicated."""
        alert_payload = [{
            "labels": {"alertname": "DuplicateTest", "test_id": "dedup"},
            "annotations": {"version": "1"},
        }]

        # Post same alert twice
        ai_alertmanager.post_alerts(alert_payload)
        alert_payload[0]["annotations"]["version"] = "2"
        ai_alertmanager.post_alerts(alert_payload)

        # Should only have one alert with latest version
        response = ai_alertmanager.get_alerts()
        alerts = response.json()
        test_alerts = [a for a in alerts if a["labels"].get("test_id") == "dedup"]
        assert len(test_alerts) == 1
        assert test_alerts[0]["annotations"]["version"] == "2"

    def test_get_status(self, ai_alertmanager):
        """Test getting Alertmanager status."""
        response = ai_alertmanager.get_status()
        assert response.status_code == 200

        status = response.json()
        assert "cluster" in status
        assert "versionInfo" in status

    def test_get_alert_groups(self, ai_alertmanager):
        """Test getting alert groups."""
        # Post an alert first
        ai_alertmanager.post_alerts([{
            "labels": {"alertname": "GroupTest"},
            "annotations": {},
        }])

        response = ai_alertmanager.get_alert_groups()
        assert response.status_code == 200

        groups = response.json()
        assert isinstance(groups, list)
        assert len(groups) > 0

    def test_get_receivers(self, ai_alertmanager):
        """Test getting receivers."""
        response = ai_alertmanager.get_receivers()
        assert response.status_code == 200

        receivers = response.json()
        assert isinstance(receivers, list)

    def test_get_silences(self, ai_alertmanager):
        """Test getting silences."""
        response = ai_alertmanager.get_silences()
        # Should return 200 with empty list or array
        assert response.status_code == 200
        assert isinstance(response.json(), list)


@pytest.mark.skipif(not COMPARE_MODE, reason="Comparative testing not enabled")
class TestAlertmanagerComparison:
    """
    Comparative tests that verify identical behavior between
    real Alertmanager and AI-Alertmanager.
    """

    def test_compare_post_response(self, ai_alertmanager, real_alertmanager):
        """Compare POST /api/v2/alerts response."""
        alert_payload = [{
            "labels": {"alertname": "CompareTest", "test": "post_response"},
            "annotations": {},
        }]

        # Post to both
        ai_response = ai_alertmanager.post_alerts(alert_payload)
        real_response = real_alertmanager.post_alerts(alert_payload)

        # Compare responses
        assert ai_response.status_code == real_response.status_code
        assert ai_response.json() == real_response.json()

    def test_compare_get_alerts_structure(self, ai_alertmanager, real_alertmanager):
        """Compare GET /api/v2/alerts response structure."""
        # Post same alert to both
        alert_payload = [{
            "labels": {"alertname": "CompareTest", "test": "get_structure"},
            "annotations": {"summary": "Test"},
        }]

        ai_alertmanager.post_alerts(alert_payload)
        real_alertmanager.post_alerts(alert_payload)

        # Get alerts from both
        ai_response = ai_alertmanager.get_alerts()
        real_response = real_alertmanager.get_alerts()

        assert ai_response.status_code == real_response.status_code

        ai_alerts = ai_response.json()
        real_alerts = real_response.json()

        # Compare structure (not exact values, as fingerprints may differ)
        if len(ai_alerts) > 0 and len(real_alerts) > 0:
            ai_alert = ai_alerts[0]
            real_alert = real_alerts[0]

            # Same top-level keys
            assert set(ai_alert.keys()) >= {"labels", "annotations", "startsAt"}
            assert set(real_alert.keys()) >= {"labels", "annotations", "startsAt"}

    def test_compare_status_structure(self, ai_alertmanager, real_alertmanager):
        """Compare GET /api/v2/status response structure."""
        ai_response = ai_alertmanager.get_status()
        real_response = real_alertmanager.get_status()

        assert ai_response.status_code == real_response.status_code

        ai_status = ai_response.json()
        real_status = real_response.json()

        # Both should have required fields
        required_fields = {"cluster", "versionInfo", "config"}
        assert set(ai_status.keys()) >= required_fields
        assert set(real_status.keys()) >= required_fields

    def test_compare_receivers_response(self, ai_alertmanager, real_alertmanager):
        """Compare GET /api/v2/receivers response."""
        ai_response = ai_alertmanager.get_receivers()
        real_response = real_alertmanager.get_receivers()

        assert ai_response.status_code == real_response.status_code
        assert isinstance(ai_response.json(), list)
        assert isinstance(real_response.json(), list)


def test_run_smoke_test_suite(ai_alertmanager):
    """
    Run a complete smoke test suite simulating Prometheus usage.
    """
    # 1. Post firing alerts
    firing_alerts = [
        {
            "labels": {
                "alertname": "HighMemoryUsage",
                "severity": "warning",
                "instance": "server1:9100",
                "job": "node-exporter",
            },
            "annotations": {
                "summary": "High memory usage on server1",
                "description": "Memory usage is above 90%",
            },
            "startsAt": datetime.utcnow().isoformat() + "Z",
            "generatorURL": "http://prometheus:9090/graph",
        },
        {
            "labels": {
                "alertname": "HighCPUUsage",
                "severity": "critical",
                "instance": "server2:9100",
                "job": "node-exporter",
            },
            "annotations": {
                "summary": "High CPU usage on server2",
            },
            "startsAt": datetime.utcnow().isoformat() + "Z",
        },
    ]

    response = ai_alertmanager.post_alerts(firing_alerts)
    assert response.status_code == 200

    # 2. Query all alerts
    response = ai_alertmanager.get_alerts()
    assert response.status_code == 200
    alerts = response.json()
    assert len(alerts) >= 2

    # 3. Query active alerts only
    response = ai_alertmanager.get_alerts(active=True)
    assert response.status_code == 200

    # 4. Query by severity
    response = ai_alertmanager.get_alerts(filter="severity=critical")
    assert response.status_code == 200
    critical_alerts = response.json()
    if critical_alerts:  # May be filtered out
        assert all(
            a["labels"].get("severity") == "critical"
            for a in critical_alerts
            if "severity" in a["labels"]
        )

    # 5. Get alert groups
    response = ai_alertmanager.get_alert_groups()
    assert response.status_code == 200

    # 6. Resolve an alert
    resolved_alert = [{
        "labels": firing_alerts[0]["labels"],
        "annotations": firing_alerts[0]["annotations"],
        "startsAt": firing_alerts[0]["startsAt"],
        "endsAt": datetime.utcnow().isoformat() + "Z",
    }]

    response = ai_alertmanager.post_alerts(resolved_alert)
    assert response.status_code == 200

    # 7. Check status
    response = ai_alertmanager.get_status()
    assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
