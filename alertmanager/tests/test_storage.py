"""
Tests for alert storage functionality.
"""

import pytest
from datetime import datetime, timedelta

from app.models import Alert, HolmesInvestigation
from app.storage import AlertStorage


@pytest.fixture
def storage():
    """Create a fresh storage instance for each test."""
    return AlertStorage()


@pytest.fixture
def sample_alert():
    """Create a sample alert."""
    return Alert(
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
        generatorURL="http://prometheus:9090/graph",
    )


def test_compute_fingerprint_consistency(storage):
    """Test that fingerprint is consistent for same labels."""
    labels1 = {"alertname": "Test", "severity": "warning"}
    labels2 = {"alertname": "Test", "severity": "warning"}

    fp1 = storage.compute_fingerprint(labels1)
    fp2 = storage.compute_fingerprint(labels2)

    assert fp1 == fp2


def test_compute_fingerprint_different(storage):
    """Test that different labels produce different fingerprints."""
    labels1 = {"alertname": "Test1", "severity": "warning"}
    labels2 = {"alertname": "Test2", "severity": "warning"}

    fp1 = storage.compute_fingerprint(labels1)
    fp2 = storage.compute_fingerprint(labels2)

    assert fp1 != fp2


def test_compute_fingerprint_order_independent(storage):
    """Test that label order doesn't affect fingerprint."""
    labels1 = {"alertname": "Test", "severity": "warning", "namespace": "prod"}
    labels2 = {"namespace": "prod", "alertname": "Test", "severity": "warning"}

    fp1 = storage.compute_fingerprint(labels1)
    fp2 = storage.compute_fingerprint(labels2)

    assert fp1 == fp2


def test_add_alert(storage, sample_alert):
    """Test adding an alert."""
    fingerprint = storage.add_or_update_alert(sample_alert)

    assert fingerprint is not None
    assert len(storage.alerts) == 1
    assert storage.alerts[fingerprint] == sample_alert
    assert sample_alert.fingerprint == fingerprint


def test_update_alert(storage, sample_alert):
    """Test updating an existing alert."""
    # Add alert
    fingerprint1 = storage.add_or_update_alert(sample_alert)

    # Update same alert (same labels, different annotations)
    sample_alert.annotations["updated"] = "true"
    fingerprint2 = storage.add_or_update_alert(sample_alert)

    # Should have same fingerprint
    assert fingerprint1 == fingerprint2
    assert len(storage.alerts) == 1
    assert storage.alerts[fingerprint1].annotations["updated"] == "true"


def test_get_alert(storage, sample_alert):
    """Test getting an alert by fingerprint."""
    fingerprint = storage.add_or_update_alert(sample_alert)

    retrieved = storage.get_alert(fingerprint)
    assert retrieved is not None
    assert retrieved.labels == sample_alert.labels


def test_get_alert_not_found(storage):
    """Test getting a non-existent alert."""
    result = storage.get_alert("nonexistent")
    assert result is None


def test_get_all_alerts_empty(storage):
    """Test getting alerts from empty storage."""
    alerts = storage.get_all_alerts()
    assert alerts == []


def test_get_all_alerts(storage):
    """Test getting all alerts."""
    # Add multiple alerts
    alert1 = Alert(
        labels={"alertname": "Alert1"},
        annotations={},
        startsAt=datetime.utcnow(),
    )
    alert2 = Alert(
        labels={"alertname": "Alert2"},
        annotations={},
        startsAt=datetime.utcnow(),
    )

    storage.add_or_update_alert(alert1)
    storage.add_or_update_alert(alert2)

    alerts = storage.get_all_alerts()
    assert len(alerts) == 2


def test_get_all_alerts_with_label_filter(storage):
    """Test filtering alerts by labels."""
    # Add alerts with different labels
    alert1 = Alert(
        labels={"alertname": "Alert1", "severity": "warning"},
        annotations={},
        startsAt=datetime.utcnow(),
    )
    alert2 = Alert(
        labels={"alertname": "Alert2", "severity": "critical"},
        annotations={},
        startsAt=datetime.utcnow(),
    )

    storage.add_or_update_alert(alert1)
    storage.add_or_update_alert(alert2)

    # Filter by severity
    filtered = storage.get_all_alerts(filter_labels={"severity": "critical"})
    assert len(filtered) == 1
    assert filtered[0].labels["alertname"] == "Alert2"


def test_get_all_alerts_active_filter(storage):
    """Test filtering alerts by active status."""
    now = datetime.utcnow()

    # Active alert (no endsAt)
    alert1 = Alert(
        labels={"alertname": "Alert1"},
        annotations={},
        startsAt=now,
        endsAt=None,
    )

    # Resolved alert (endsAt in past)
    alert2 = Alert(
        labels={"alertname": "Alert2"},
        annotations={},
        startsAt=now - timedelta(hours=2),
        endsAt=now - timedelta(hours=1),
    )

    storage.add_or_update_alert(alert1)
    storage.add_or_update_alert(alert2)

    # Get only active alerts
    active = storage.get_all_alerts(active=True)
    assert len(active) == 1
    assert active[0].labels["alertname"] == "Alert1"

    # Get only resolved alerts
    resolved = storage.get_all_alerts(active=False)
    assert len(resolved) == 1
    assert resolved[0].labels["alertname"] == "Alert2"


def test_delete_alert(storage, sample_alert):
    """Test deleting an alert."""
    fingerprint = storage.add_or_update_alert(sample_alert)

    # Delete the alert
    result = storage.delete_alert(fingerprint)
    assert result is True
    assert len(storage.alerts) == 0
    assert storage.get_alert(fingerprint) is None


def test_delete_alert_not_found(storage):
    """Test deleting a non-existent alert."""
    result = storage.delete_alert("nonexistent")
    assert result is False


def test_add_investigation(storage, sample_alert):
    """Test adding an investigation."""
    fingerprint = storage.add_or_update_alert(sample_alert)

    investigation = HolmesInvestigation(
        alert_fingerprint=fingerprint,
        investigation_status="completed",
        analysis="Root cause identified",
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )

    storage.add_investigation(fingerprint, investigation)

    assert len(storage.investigations) == 1
    assert storage.investigations[fingerprint] == investigation


def test_get_investigation(storage, sample_alert):
    """Test getting an investigation."""
    fingerprint = storage.add_or_update_alert(sample_alert)

    investigation = HolmesInvestigation(
        alert_fingerprint=fingerprint,
        investigation_status="completed",
        started_at=datetime.utcnow(),
    )

    storage.add_investigation(fingerprint, investigation)

    retrieved = storage.get_investigation(fingerprint)
    assert retrieved is not None
    assert retrieved.investigation_status == "completed"


def test_get_investigation_not_found(storage):
    """Test getting a non-existent investigation."""
    result = storage.get_investigation("nonexistent")
    assert result is None


def test_get_pending_investigations_empty(storage):
    """Test getting pending investigations when none exist."""
    pending = storage.get_pending_investigations()
    assert pending == []


def test_get_pending_investigations(storage):
    """Test getting pending investigations."""
    # Add alerts
    alert1 = Alert(
        labels={"alertname": "Alert1"},
        annotations={},
        startsAt=datetime.utcnow(),
    )
    alert2 = Alert(
        labels={"alertname": "Alert2"},
        annotations={},
        startsAt=datetime.utcnow(),
    )

    fp1 = storage.add_or_update_alert(alert1)
    fp2 = storage.add_or_update_alert(alert2)

    # Mark first as completed
    inv1 = HolmesInvestigation(
        alert_fingerprint=fp1,
        investigation_status="completed",
        started_at=datetime.utcnow(),
    )
    storage.add_investigation(fp1, inv1)

    # Mark second as pending
    inv2 = HolmesInvestigation(
        alert_fingerprint=fp2,
        investigation_status="pending",
        started_at=datetime.utcnow(),
    )
    storage.add_investigation(fp2, inv2)

    # Get pending investigations
    pending = storage.get_pending_investigations()
    assert len(pending) == 1
    assert pending[0][0] == fp2


def test_clear_storage(storage, sample_alert):
    """Test clearing all storage."""
    storage.add_or_update_alert(sample_alert)

    investigation = HolmesInvestigation(
        alert_fingerprint="test",
        investigation_status="pending",
        started_at=datetime.utcnow(),
    )
    storage.add_investigation("test", investigation)

    storage.clear()

    assert len(storage.alerts) == 0
    assert len(storage.investigations) == 0
    assert len(storage.label_index) == 0
