"""
In-memory storage for alerts and investigations.

This provides a simple in-memory storage implementation. In production,
this should be replaced with a persistent storage backend (e.g., PostgreSQL).
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional
from collections import defaultdict

from .models import Alert, HolmesInvestigation

logger = logging.getLogger(__name__)


class AlertStorage:
    """
    In-memory storage for alerts with investigation tracking.
    """

    def __init__(self):
        """Initialize empty storage."""
        # Dictionary mapping fingerprint -> Alert
        self.alerts: Dict[str, Alert] = {}

        # Dictionary mapping fingerprint -> HolmesInvestigation
        self.investigations: Dict[str, HolmesInvestigation] = {}

        # Index for faster label-based queries
        self.label_index: Dict[str, set] = defaultdict(set)

    def compute_fingerprint(self, labels: Dict[str, str]) -> str:
        """
        Compute a unique fingerprint for an alert based on its labels.

        This matches Alertmanager's fingerprinting algorithm.

        Args:
            labels: Alert labels

        Returns:
            Hex-encoded fingerprint
        """
        # Sort labels for consistent fingerprinting
        sorted_labels = sorted(labels.items())
        label_str = json.dumps(sorted_labels, sort_keys=True)
        return hashlib.sha256(label_str.encode()).hexdigest()[:16]

    def add_or_update_alert(self, alert: Alert) -> str:
        """
        Add or update an alert in storage.

        Args:
            alert: Alert to add/update

        Returns:
            Alert fingerprint
        """
        fingerprint = self.compute_fingerprint(alert.labels)
        alert.fingerprint = fingerprint

        # Update label index
        for key, value in alert.labels.items():
            self.label_index[f"{key}={value}"].add(fingerprint)

        self.alerts[fingerprint] = alert
        logger.info(f"Stored alert: {alert.labels.get('alertname', 'unknown')} (fingerprint: {fingerprint})")

        return fingerprint

    def get_alert(self, fingerprint: str) -> Optional[Alert]:
        """
        Get an alert by fingerprint.

        Args:
            fingerprint: Alert fingerprint

        Returns:
            Alert if found, None otherwise
        """
        return self.alerts.get(fingerprint)

    def get_all_alerts(
        self,
        active: Optional[bool] = None,
        silenced: Optional[bool] = None,
        inhibited: Optional[bool] = None,
        filter_labels: Optional[Dict[str, str]] = None,
    ) -> List[Alert]:
        """
        Get all alerts matching the given filters.

        Args:
            active: If True, only return active alerts
            silenced: If True, only return silenced alerts
            inhibited: If True, only return inhibited alerts
            filter_labels: Label filters to apply

        Returns:
            List of matching alerts
        """
        alerts = list(self.alerts.values())

        # Filter by labels if specified
        if filter_labels:
            alerts = [
                alert
                for alert in alerts
                if all(alert.labels.get(k) == v for k, v in filter_labels.items())
            ]

        # Filter by active status
        if active is not None:
            now = datetime.now(timezone.utc)
            alerts = [
                alert
                for alert in alerts
                if (alert.endsAt is None or alert.endsAt > now) == active
            ]

        # TODO: Implement silenced and inhibited filtering
        # For now, we don't support silences and inhibition rules

        return alerts

    def delete_alert(self, fingerprint: str) -> bool:
        """
        Delete an alert from storage.

        Args:
            fingerprint: Alert fingerprint

        Returns:
            True if deleted, False if not found
        """
        if fingerprint in self.alerts:
            alert = self.alerts[fingerprint]

            # Remove from label index
            for key, value in alert.labels.items():
                self.label_index[f"{key}={value}"].discard(fingerprint)

            del self.alerts[fingerprint]
            logger.info(f"Deleted alert: fingerprint={fingerprint}")
            return True

        return False

    def add_investigation(
        self,
        fingerprint: str,
        investigation: HolmesInvestigation
    ) -> None:
        """
        Add or update an investigation result.

        Args:
            fingerprint: Alert fingerprint
            investigation: Investigation result
        """
        self.investigations[fingerprint] = investigation
        logger.info(f"Stored investigation for alert: fingerprint={fingerprint}, status={investigation.investigation_status}")

    def get_investigation(self, fingerprint: str) -> Optional[HolmesInvestigation]:
        """
        Get investigation result for an alert.

        Args:
            fingerprint: Alert fingerprint

        Returns:
            Investigation result if found, None otherwise
        """
        return self.investigations.get(fingerprint)

    def get_pending_investigations(self) -> List[tuple[str, Alert]]:
        """
        Get all alerts that need investigation.

        Returns:
            List of (fingerprint, alert) tuples
        """
        pending = []
        for fingerprint, alert in self.alerts.items():
            # Check if investigation is pending or not started
            investigation = self.investigations.get(fingerprint)
            if investigation is None:
                pending.append((fingerprint, alert))
            elif investigation.investigation_status == "pending":
                pending.append((fingerprint, alert))

        return pending

    def clear(self) -> None:
        """Clear all alerts and investigations."""
        self.alerts.clear()
        self.investigations.clear()
        self.label_index.clear()
        logger.info("Cleared all alerts and investigations")
