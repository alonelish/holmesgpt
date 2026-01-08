"""
Pydantic models for Alertmanager v2 API compatibility.

These models match the Alertmanager v2 API specification to ensure
100% compatibility with Prometheus and Prometheus Operator.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class Alert(BaseModel):
    """
    Alert model compatible with Alertmanager v2 API.

    See: https://github.com/prometheus/alertmanager/blob/main/api/v2/models/alert.go
    """
    labels: Dict[str, str] = Field(default_factory=dict, description="Labels attached to the alert")
    annotations: Dict[str, str] = Field(default_factory=dict, description="Annotations attached to the alert")
    startsAt: datetime = Field(description="Start time of the alert")
    endsAt: Optional[datetime] = Field(None, description="End time of the alert (if resolved)")
    generatorURL: Optional[str] = Field(None, description="URL of the alert generator")

    # Additional fields for internal use
    fingerprint: Optional[str] = Field(None, description="Unique fingerprint for deduplication")
    status: Optional[Dict[str, Any]] = Field(None, description="Status information")
    receivers: Optional[List[Dict[str, str]]] = Field(None, description="Receivers that handled the alert")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "labels": {
                    "alertname": "HighMemoryUsage",
                    "severity": "warning",
                    "namespace": "production"
                },
                "annotations": {
                    "summary": "High memory usage detected",
                    "description": "Memory usage is above 90%"
                },
                "startsAt": "2024-01-01T12:00:00Z",
                "generatorURL": "http://prometheus:9090/graph?g0.expr=..."
            }
        }
    )


class AlertGroup(BaseModel):
    """
    Alert group model for grouping related alerts.
    """
    labels: Dict[str, str] = Field(default_factory=dict)
    receiver: Dict[str, str] = Field(default_factory=dict)
    alerts: List[Alert] = Field(default_factory=list)


class Silence(BaseModel):
    """
    Silence model compatible with Alertmanager v2 API.
    """
    id: Optional[str] = Field(None, description="Silence ID")
    matchers: List[Dict[str, str]] = Field(default_factory=list, description="Label matchers")
    startsAt: datetime = Field(description="Start time of the silence")
    endsAt: datetime = Field(description="End time of the silence")
    createdBy: str = Field(description="Creator of the silence")
    comment: str = Field(description="Comment about the silence")
    status: Optional[Dict[str, str]] = Field(None, description="Status of the silence")


class Receiver(BaseModel):
    """
    Receiver configuration model.
    """
    name: str = Field(description="Name of the receiver")


class Status(BaseModel):
    """
    Alertmanager status model.
    """
    cluster: Dict[str, Any] = Field(default_factory=dict)
    versionInfo: Dict[str, str] = Field(default_factory=dict)
    config: Dict[str, Any] = Field(default_factory=dict)
    uptime: str = Field(default="")


class PostableAlert(BaseModel):
    """
    Postable alert model for creating/updating alerts.
    This is what Prometheus sends to Alertmanager.
    """
    labels: Dict[str, str] = Field(default_factory=dict)
    annotations: Dict[str, str] = Field(default_factory=dict)
    startsAt: Optional[datetime] = None
    endsAt: Optional[datetime] = None
    generatorURL: Optional[str] = None


class AlertResponse(BaseModel):
    """
    Response model for alert queries.
    """
    status: str = Field(default="success")
    data: List[Alert] = Field(default_factory=list)


class HolmesInvestigation(BaseModel):
    """
    Holmes AI investigation result model.
    """
    alert_fingerprint: str = Field(description="Alert fingerprint")
    investigation_status: str = Field(description="Status: pending, investigating, completed, failed")
    analysis: Optional[str] = Field(None, description="AI analysis result")
    root_cause: Optional[str] = Field(None, description="Root cause if identified")
    recommendations: Optional[List[str]] = Field(None, description="Recommended actions")
    started_at: datetime = Field(description="Investigation start time")
    completed_at: Optional[datetime] = Field(None, description="Investigation completion time")
    error: Optional[str] = Field(None, description="Error message if investigation failed")
