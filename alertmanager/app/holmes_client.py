"""
HolmesGPT API client for AI-powered alert investigations.
"""

import logging
from typing import Dict, Any, Optional
import httpx
from datetime import datetime

logger = logging.getLogger(__name__)


class HolmesClient:
    """
    Client for interacting with HolmesGPT investigation API.
    """

    def __init__(self, base_url: str, timeout: int = 300):
        """
        Initialize Holmes client.

        Args:
            base_url: Base URL of HolmesGPT server (e.g., "http://holmes:8080")
            timeout: Request timeout in seconds (default: 300s for long investigations)
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)

    async def investigate_alert(
        self,
        alert_name: str,
        alert_labels: Dict[str, str],
        alert_annotations: Dict[str, str],
        starts_at: datetime,
        generator_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Investigate an alert using HolmesGPT.

        Args:
            alert_name: Name of the alert (from alertname label)
            alert_labels: Alert labels
            alert_annotations: Alert annotations
            starts_at: Alert start time
            generator_url: Prometheus generator URL

        Returns:
            Investigation result from HolmesGPT

        Raises:
            httpx.HTTPError: If the request fails
        """
        # Build description from labels and annotations
        description_parts = []

        # Add summary from annotations if available
        if "summary" in alert_annotations:
            description_parts.append(alert_annotations["summary"])

        # Add description from annotations if available
        if "description" in alert_annotations:
            description_parts.append(alert_annotations["description"])

        # Add key labels
        for key, value in alert_labels.items():
            if key not in ["alertname", "__name__"]:
                description_parts.append(f"{key}={value}")

        description = " | ".join(description_parts) if description_parts else alert_name

        # Build context with all available information
        context = {
            "labels": alert_labels,
            "annotations": alert_annotations,
            "startsAt": starts_at.isoformat(),
            "alertname": alert_name,
        }

        if generator_url:
            context["generatorURL"] = generator_url

        # Build investigation request
        request_payload = {
            "source": "alertmanager",
            "title": alert_name,
            "description": description,
            "subject": {
                "name": alert_name,
                "type": "alert",
                "namespace": alert_labels.get("namespace", "default"),
            },
            "context": context,
            "source_instance_id": "ai-alertmanager",
        }

        logger.info(f"Investigating alert: {alert_name}")
        logger.debug(f"Investigation request: {request_payload}")

        try:
            response = await self.client.post(
                f"{self.base_url}/api/investigate",
                json=request_payload,
            )
            response.raise_for_status()
            result = response.json()

            logger.info(f"Investigation completed for alert: {alert_name}")
            logger.debug(f"Investigation result: {result}")

            return result

        except httpx.HTTPError as e:
            logger.error(f"Failed to investigate alert {alert_name}: {e}")
            raise

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    def format_investigation_for_label(self, investigation_result: Dict[str, Any]) -> str:
        """
        Format investigation result into a compact string suitable for alert label.

        Args:
            investigation_result: Result from HolmesGPT investigation

        Returns:
            Formatted string for the ai_investigation label
        """
        analysis = investigation_result.get("analysis", "")

        # Extract key findings (first paragraph or first 200 chars)
        if analysis:
            # Take first paragraph or first 200 characters
            summary = analysis.split("\n\n")[0] if "\n\n" in analysis else analysis
            if len(summary) > 200:
                summary = summary[:197] + "..."
            return summary

        return "Investigation completed - no analysis available"

    def extract_root_cause(self, investigation_result: Dict[str, Any]) -> Optional[str]:
        """
        Extract root cause from investigation result.

        Args:
            investigation_result: Result from HolmesGPT investigation

        Returns:
            Root cause string if identified, None otherwise
        """
        analysis = investigation_result.get("analysis", "")

        # Look for common root cause indicators in the analysis
        root_cause_keywords = [
            "root cause:",
            "caused by:",
            "due to:",
            "reason:",
            "problem:",
        ]

        for keyword in root_cause_keywords:
            if keyword in analysis.lower():
                # Extract the sentence containing the keyword
                idx = analysis.lower().index(keyword)
                # Get the rest of the sentence
                rest = analysis[idx:].split(".")[0]
                return rest.strip()

        return None
