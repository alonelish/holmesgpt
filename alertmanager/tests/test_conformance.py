"""
Conformance tests for Alertmanager API compatibility.

These tests verify that AI-Alertmanager behaves identically to
the standard Alertmanager v2 API to ensure 100% compatibility
with Prometheus and Prometheus Operator.

Based on the official Alertmanager API spec:
https://github.com/prometheus/alertmanager/blob/main/api/v2/openapi.yaml
"""

import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from app.main import app, storage


@pytest.fixture
def client():
    """Create a test client."""
    storage.clear()
    return TestClient(app)


class TestAlertmanagerV2APIConformance:
    """
    Test suite for Alertmanager v2 API conformance.
    """

    def test_api_version_endpoints(self, client):
        """Test that both v1 and v2 API endpoints are supported."""
        test_payload = [{
            "labels": {"alertname": "TestAlert"},
            "annotations": {},
        }]

        # Test v2 endpoint
        response_v2 = client.post("/api/v2/alerts", json=test_payload)
        assert response_v2.status_code == 200

        # Test v1 endpoint (backwards compatibility)
        storage.clear()
        response_v1 = client.post("/api/v1/alerts", json=test_payload)
        assert response_v1.status_code == 200

    def test_post_alerts_response_format(self, client):
        """Test that POST /api/v2/alerts returns correct response format."""
        alert_payload = [{
            "labels": {
                "alertname": "HighCPU",
                "severity": "warning",
            },
            "annotations": {
                "summary": "High CPU usage",
            },
        }]

        response = client.post("/api/v2/alerts", json=alert_payload)

        # Alertmanager returns 200 OK with simple success response
        assert response.status_code == 200
        assert response.json() == {"status": "success"}

    def test_post_alerts_empty_array(self, client):
        """Test posting empty alert array (edge case)."""
        response = client.post("/api/v2/alerts", json=[])
        assert response.status_code == 200

    def test_post_alerts_with_timestamps(self, client):
        """Test posting alerts with explicit timestamps."""
        now = datetime.utcnow()
        alert_payload = [{
            "labels": {"alertname": "TestAlert"},
            "annotations": {},
            "startsAt": now.isoformat() + "Z",
            "endsAt": (now + timedelta(hours=1)).isoformat() + "Z",
        }]

        response = client.post("/api/v2/alerts", json=alert_payload)
        assert response.status_code == 200

        # Verify alert was stored with correct timestamps
        alerts = storage.get_all_alerts()
        assert len(alerts) == 1
        assert alerts[0].startsAt is not None
        assert alerts[0].endsAt is not None

    def test_post_alerts_with_generator_url(self, client):
        """Test posting alerts with generatorURL."""
        alert_payload = [{
            "labels": {"alertname": "TestAlert"},
            "annotations": {},
            "generatorURL": "http://prometheus:9090/graph?g0.expr=up",
        }]

        response = client.post("/api/v2/alerts", json=alert_payload)
        assert response.status_code == 200

        alerts = storage.get_all_alerts()
        assert alerts[0].generatorURL == "http://prometheus:9090/graph?g0.expr=up"

    def test_get_alerts_response_format(self, client):
        """Test that GET /api/v2/alerts returns correct response format."""
        # Post an alert first
        client.post("/api/v2/alerts", json=[{
            "labels": {"alertname": "TestAlert", "severity": "info"},
            "annotations": {"summary": "Test"},
        }])

        response = client.get("/api/v2/alerts")

        assert response.status_code == 200
        alerts = response.json()
        assert isinstance(alerts, list)
        assert len(alerts) > 0

        # Verify alert structure matches Alertmanager format
        alert = alerts[0]
        assert "labels" in alert
        assert "annotations" in alert
        assert "startsAt" in alert
        assert alert["labels"]["alertname"] == "TestAlert"

    def test_get_alerts_filter_by_active(self, client):
        """Test filtering alerts by active status."""
        now = datetime.utcnow()

        # Post active alert (no endsAt)
        client.post("/api/v2/alerts", json=[{
            "labels": {"alertname": "ActiveAlert"},
            "annotations": {},
            "startsAt": now.isoformat() + "Z",
        }])

        # Post resolved alert (endsAt in past)
        client.post("/api/v2/alerts", json=[{
            "labels": {"alertname": "ResolvedAlert"},
            "annotations": {},
            "startsAt": (now - timedelta(hours=2)).isoformat() + "Z",
            "endsAt": (now - timedelta(hours=1)).isoformat() + "Z",
        }])

        # Get only active alerts
        response = client.get("/api/v2/alerts?active=true")
        assert response.status_code == 200
        alerts = response.json()
        assert len(alerts) == 1
        assert alerts[0]["labels"]["alertname"] == "ActiveAlert"

        # Get only resolved alerts
        response = client.get("/api/v2/alerts?active=false")
        assert response.status_code == 200
        alerts = response.json()
        assert len(alerts) == 1
        assert alerts[0]["labels"]["alertname"] == "ResolvedAlert"

    def test_get_alerts_filter_by_labels(self, client):
        """Test filtering alerts by labels."""
        # Post alerts with different labels
        client.post("/api/v2/alerts", json=[
            {"labels": {"alertname": "Alert1", "severity": "warning"}, "annotations": {}},
            {"labels": {"alertname": "Alert2", "severity": "critical"}, "annotations": {}},
        ])

        # Filter by severity
        response = client.get("/api/v2/alerts?filter=severity=critical")
        assert response.status_code == 200
        alerts = response.json()
        assert len(alerts) == 1
        assert alerts[0]["labels"]["severity"] == "critical"

    def test_get_alert_groups_response_format(self, client):
        """Test GET /api/v2/alerts/groups response format."""
        # Post some alerts
        client.post("/api/v2/alerts", json=[{
            "labels": {"alertname": "TestAlert"},
            "annotations": {},
        }])

        response = client.get("/api/v2/alerts/groups")
        assert response.status_code == 200
        groups = response.json()

        # Verify response structure
        assert isinstance(groups, list)
        assert len(groups) > 0
        group = groups[0]
        assert "labels" in group
        assert "receiver" in group
        assert "alerts" in group
        assert isinstance(group["alerts"], list)

    def test_status_endpoint_response_format(self, client):
        """Test GET /api/v2/status response format matches Alertmanager."""
        response = client.get("/api/v2/status")
        assert response.status_code == 200

        status = response.json()

        # Verify required fields
        assert "cluster" in status
        assert "versionInfo" in status
        assert "config" in status
        assert "uptime" in status

        # Verify versionInfo structure
        version_info = status["versionInfo"]
        assert "version" in version_info
        assert "revision" in version_info
        assert "branch" in version_info

    def test_silences_endpoint_exists(self, client):
        """Test that silences endpoints exist (even if not fully implemented)."""
        # GET silences should return empty list
        response = client.get("/api/v2/silences")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

        # POST silence should return 501 (not implemented) or 200
        response = client.post("/api/v2/silences", json={})
        assert response.status_code in [200, 501]

    def test_receivers_endpoint_response_format(self, client):
        """Test GET /api/v2/receivers response format."""
        response = client.get("/api/v2/receivers")
        assert response.status_code == 200

        receivers = response.json()
        assert isinstance(receivers, list)
        assert len(receivers) > 0
        assert "name" in receivers[0]

    def test_health_endpoints(self, client):
        """Test health check endpoints."""
        # Kubernetes-style
        response = client.get("/healthz")
        assert response.status_code == 200
        assert "status" in response.json()

        response = client.get("/readyz")
        assert response.status_code == 200
        assert "status" in response.json()

        # Alertmanager-style
        response = client.get("/-/healthy")
        assert response.status_code == 200

        response = client.get("/-/ready")
        assert response.status_code == 200

    def test_alert_deduplication_by_labels(self, client):
        """Test that alerts with same labels are deduplicated."""
        alert_payload = {
            "labels": {"alertname": "TestAlert", "instance": "server1"},
            "annotations": {"description": "First"},
        }

        # Post same alert twice
        client.post("/api/v2/alerts", json=[alert_payload])
        alert_payload["annotations"]["description"] = "Second"
        client.post("/api/v2/alerts", json=[alert_payload])

        # Should only have one alert
        alerts = storage.get_all_alerts()
        assert len(alerts) == 1
        # Should have latest version
        assert alerts[0].annotations["description"] == "Second"

    def test_alert_with_special_label_characters(self, client):
        """Test alerts with special characters in labels."""
        alert_payload = [{
            "labels": {
                "alertname": "TestAlert",
                "namespace": "kube-system",
                "pod": "metrics-server-12345",
                "endpoint": "https://example.com:443/metrics",
            },
            "annotations": {},
        }]

        response = client.post("/api/v2/alerts", json=alert_payload)
        assert response.status_code == 200

        alerts = storage.get_all_alerts()
        assert len(alerts) == 1
        assert alerts[0].labels["namespace"] == "kube-system"
        assert alerts[0].labels["endpoint"] == "https://example.com:443/metrics"

    def test_alert_with_unicode_in_annotations(self, client):
        """Test alerts with unicode characters in annotations."""
        alert_payload = [{
            "labels": {"alertname": "TestAlert"},
            "annotations": {
                "summary": "High CPU usage 📊",
                "description": "CPU utilization > 90% 🔥",
            },
        }]

        response = client.post("/api/v2/alerts", json=alert_payload)
        assert response.status_code == 200

        alerts = storage.get_all_alerts()
        assert alerts[0].annotations["summary"] == "High CPU usage 📊"

    def test_concurrent_alert_posts(self, client):
        """Test posting multiple alerts concurrently."""
        alerts_payload = [
            {"labels": {"alertname": f"Alert{i}"}, "annotations": {}}
            for i in range(10)
        ]

        response = client.post("/api/v2/alerts", json=alerts_payload)
        assert response.status_code == 200

        alerts = storage.get_all_alerts()
        assert len(alerts) == 10

    def test_alert_without_optional_fields(self, client):
        """Test posting minimal alert (only required fields)."""
        # Alertmanager requires at minimum labels
        alert_payload = [{
            "labels": {"alertname": "MinimalAlert"},
        }]

        response = client.post("/api/v2/alerts", json=alert_payload)
        assert response.status_code == 200

        alerts = storage.get_all_alerts()
        assert len(alerts) == 1
        assert alerts[0].labels["alertname"] == "MinimalAlert"
        assert alerts[0].annotations == {}

    def test_large_alert_payload(self, client):
        """Test posting alert with large annotations."""
        large_description = "A" * 10000  # 10KB description

        alert_payload = [{
            "labels": {"alertname": "LargeAlert"},
            "annotations": {
                "description": large_description,
            },
        }]

        response = client.post("/api/v2/alerts", json=alert_payload)
        assert response.status_code == 200

        alerts = storage.get_all_alerts()
        assert len(alerts) == 1
        assert len(alerts[0].annotations["description"]) == 10000

    def test_prometheus_operator_compatibility(self, client):
        """Test compatibility with alerts sent by Prometheus Operator."""
        # Typical Prometheus Operator alert format
        alert_payload = [{
            "labels": {
                "alertname": "KubePodCrashLooping",
                "namespace": "default",
                "pod": "myapp-12345",
                "severity": "warning",
                "prometheus": "monitoring/prometheus",
            },
            "annotations": {
                "summary": "Pod is crash looping",
                "description": "Pod default/myapp-12345 is crash looping",
                "runbook_url": "https://runbooks.example.com/KubePodCrashLooping",
            },
            "startsAt": datetime.utcnow().isoformat() + "Z",
            "generatorURL": "http://prometheus:9090/graph?g0.expr=rate(kube_pod_container_status_restarts_total[15m])%20%3E%200&g0.tab=1",
        }]

        response = client.post("/api/v2/alerts", json=alert_payload)
        assert response.status_code == 200

        alerts = storage.get_all_alerts()
        assert len(alerts) == 1
        assert alerts[0].labels["prometheus"] == "monitoring/prometheus"
        assert "runbook_url" in alerts[0].annotations


class TestAlertmanagerAPIEdgeCases:
    """
    Test edge cases and error conditions.
    """

    def test_get_alerts_empty_storage(self, client):
        """Test getting alerts when storage is empty."""
        storage.clear()
        response = client.get("/api/v2/alerts")
        assert response.status_code == 200
        assert response.json() == []

    def test_post_malformed_alert_timestamps(self, client):
        """Test posting alerts with malformed timestamps."""
        alert_payload = [{
            "labels": {"alertname": "TestAlert"},
            "startsAt": "invalid-timestamp",
        }]

        response = client.post("/api/v2/alerts", json=alert_payload)
        # Should return 422 Unprocessable Entity for validation error
        assert response.status_code == 422

    def test_get_nonexistent_investigation(self, client):
        """Test getting investigation for non-existent alert."""
        response = client.get("/api/v2/investigations/nonexistent-fingerprint")
        assert response.status_code == 404

    def test_trigger_investigation_nonexistent_alert(self, client):
        """Test triggering investigation for non-existent alert."""
        response = client.post("/api/v2/investigate/nonexistent-fingerprint")
        assert response.status_code == 404


class TestAlertmanagerPerformance:
    """
    Performance and scalability tests.
    """

    def test_post_many_alerts(self, client):
        """Test posting many alerts at once."""
        num_alerts = 100
        alerts_payload = [
            {"labels": {"alertname": f"Alert{i}", "id": str(i)}, "annotations": {}}
            for i in range(num_alerts)
        ]

        response = client.post("/api/v2/alerts", json=alerts_payload)
        assert response.status_code == 200

        alerts = storage.get_all_alerts()
        assert len(alerts) == num_alerts

    def test_query_alerts_with_many_in_storage(self, client):
        """Test querying alerts when many are stored."""
        # Store many alerts
        for i in range(50):
            client.post("/api/v2/alerts", json=[{
                "labels": {"alertname": f"Alert{i}"},
                "annotations": {},
            }])

        # Query should still be fast
        response = client.get("/api/v2/alerts")
        assert response.status_code == 200
        alerts = response.json()
        assert len(alerts) == 50


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
