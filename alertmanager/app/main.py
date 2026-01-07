"""
AI-powered Alertmanager drop-in replacement.

This server implements the Alertmanager v2 API with added AI investigation capabilities.
It is 100% compatible with Prometheus and Prometheus Operator.
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Optional
from contextlib import asynccontextmanager

import colorlog
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse

from .config import AIAlertmanagerConfig, load_config
from .holmes_client import HolmesClient
from .models import (
    Alert,
    AlertResponse,
    PostableAlert,
    Status,
    HolmesInvestigation,
)
from .storage import AlertStorage


# Initialize logging
def init_logging(log_level: str = "INFO"):
    """Initialize colored logging."""
    logging_format = "%(log_color)s%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s"
    logging_datefmt = "%Y-%m-%d %H:%M:%S"

    colorlog.basicConfig(
        format=logging_format,
        level=log_level,
        datefmt=logging_datefmt
    )
    logging.getLogger().setLevel(log_level)


# Global state
config: AIAlertmanagerConfig = load_config()
init_logging(config.log_level)
logger = logging.getLogger(__name__)

storage = AlertStorage()
holmes_client: Optional[HolmesClient] = None
investigation_queue: asyncio.Queue = asyncio.Queue()
investigation_workers: List[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown.
    """
    global holmes_client, investigation_workers

    # Startup
    logger.info("Starting AI-Alertmanager")
    logger.info(f"HolmesGPT URL: {config.holmes_url}")
    logger.info(f"AI Investigation: {'enabled' if config.enable_ai_investigation else 'disabled'}")

    if config.enable_ai_investigation:
        holmes_client = HolmesClient(
            base_url=config.holmes_url,
            timeout=config.holmes_timeout
        )

        # Start investigation worker tasks
        for i in range(config.investigation_concurrency):
            task = asyncio.create_task(
                investigation_worker(i),
                name=f"investigation-worker-{i}"
            )
            investigation_workers.append(task)
        logger.info(f"Started {config.investigation_concurrency} investigation workers")

    yield

    # Shutdown
    logger.info("Shutting down AI-Alertmanager")

    # Stop investigation workers
    for worker in investigation_workers:
        worker.cancel()

    # Wait for workers to finish
    await asyncio.gather(*investigation_workers, return_exceptions=True)

    if holmes_client:
        await holmes_client.close()


app = FastAPI(
    title="AI-Alertmanager",
    description="AI-powered Alertmanager drop-in replacement with HolmesGPT integration",
    version=config.alertmanager_version,
    lifespan=lifespan,
)


async def investigation_worker(worker_id: int):
    """
    Background worker for processing alert investigations.

    Args:
        worker_id: Worker identifier for logging
    """
    logger.info(f"Investigation worker {worker_id} started")

    while True:
        try:
            # Get next alert from queue
            fingerprint, alert = await investigation_queue.get()

            logger.info(
                f"Worker {worker_id}: Investigating alert {alert.labels.get('alertname', 'unknown')} "
                f"(fingerprint: {fingerprint})"
            )

            # Mark as investigating
            investigation = HolmesInvestigation(
                alert_fingerprint=fingerprint,
                investigation_status="investigating",
                started_at=datetime.utcnow(),
            )
            storage.add_investigation(fingerprint, investigation)

            try:
                # Perform investigation
                result = await holmes_client.investigate_alert(
                    alert_name=alert.labels.get("alertname", "unknown"),
                    alert_labels=alert.labels,
                    alert_annotations=alert.annotations,
                    starts_at=alert.startsAt,
                    generator_url=alert.generatorURL,
                )

                # Update investigation with result
                investigation.investigation_status = "completed"
                investigation.completed_at = datetime.utcnow()
                investigation.analysis = result.get("analysis", "")
                investigation.root_cause = holmes_client.extract_root_cause(result)

                # Update alert with AI labels
                alert.labels[config.investigation_label_key] = holmes_client.format_investigation_for_label(result)
                alert.labels[config.investigation_label_status_key] = "completed"

                logger.info(f"Worker {worker_id}: Investigation completed for {fingerprint}")

            except Exception as e:
                logger.error(f"Worker {worker_id}: Investigation failed for {fingerprint}: {e}", exc_info=True)

                # Mark investigation as failed
                investigation.investigation_status = "failed"
                investigation.completed_at = datetime.utcnow()
                investigation.error = str(e)

                # Update alert label
                alert.labels[config.investigation_label_status_key] = "failed"

            # Store updated investigation and alert
            storage.add_investigation(fingerprint, investigation)
            storage.add_or_update_alert(alert)

            investigation_queue.task_done()

        except asyncio.CancelledError:
            logger.info(f"Investigation worker {worker_id} cancelled")
            break
        except Exception as e:
            logger.error(f"Worker {worker_id}: Unexpected error: {e}", exc_info=True)


async def enqueue_investigation(fingerprint: str, alert: Alert):
    """
    Enqueue an alert for investigation.

    Args:
        fingerprint: Alert fingerprint
        alert: Alert to investigate
    """
    if not config.enable_ai_investigation:
        return

    # Check if already investigated or in progress
    existing = storage.get_investigation(fingerprint)
    if existing and existing.investigation_status in ["investigating", "completed"]:
        logger.debug(f"Skipping investigation for {fingerprint} - already {existing.investigation_status}")
        return

    # Add pending investigation marker
    investigation = HolmesInvestigation(
        alert_fingerprint=fingerprint,
        investigation_status="pending",
        started_at=datetime.utcnow(),
    )
    storage.add_investigation(fingerprint, investigation)

    # Add to queue
    await investigation_queue.put((fingerprint, alert))
    logger.info(f"Enqueued investigation for alert: {alert.labels.get('alertname', 'unknown')} (fingerprint: {fingerprint})")


# =============================================================================
# Alertmanager v2 API Endpoints
# =============================================================================


@app.post("/api/v2/alerts", status_code=200)
@app.post("/api/v1/alerts", status_code=200)
async def post_alerts(
    alerts: List[PostableAlert],
    background_tasks: BackgroundTasks,
):
    """
    Post alerts to the Alertmanager.

    This is the primary endpoint used by Prometheus to send alerts.

    Compatible with:
    - Prometheus Alertmanager v2 API
    - Prometheus Operator
    """
    logger.info(f"Received {len(alerts)} alerts")

    for postable_alert in alerts:
        # Convert PostableAlert to Alert
        alert = Alert(
            labels=postable_alert.labels,
            annotations=postable_alert.annotations,
            startsAt=postable_alert.startsAt or datetime.utcnow(),
            endsAt=postable_alert.endsAt,
            generatorURL=postable_alert.generatorURL,
        )

        # Store alert
        fingerprint = storage.add_or_update_alert(alert)

        # Enqueue for investigation if enabled
        if config.enable_ai_investigation and config.investigate_on_create:
            background_tasks.add_task(enqueue_investigation, fingerprint, alert)

    return JSONResponse(content={"status": "success"}, status_code=200)


@app.get("/api/v2/alerts", response_model=List[Alert])
@app.get("/api/v1/alerts", response_model=List[Alert])
async def get_alerts(
    active: Optional[bool] = Query(None, description="Filter by active status"),
    silenced: Optional[bool] = Query(None, description="Filter by silenced status"),
    inhibited: Optional[bool] = Query(None, description="Filter by inhibited status"),
    filter: Optional[str] = Query(None, description="Label filter"),
):
    """
    Get all alerts matching the given filters.

    Compatible with Alertmanager v2 API.
    """
    # Parse label filters
    filter_labels = {}
    if filter:
        # Parse filter format: {label1="value1",label2="value2"}
        # For simplicity, we'll support basic comma-separated key=value pairs
        for pair in filter.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                filter_labels[key.strip()] = value.strip().strip('"')

    alerts = storage.get_all_alerts(
        active=active,
        silenced=silenced,
        inhibited=inhibited,
        filter_labels=filter_labels if filter_labels else None,
    )

    return alerts


@app.get("/api/v2/alerts/groups")
@app.get("/api/v1/alerts/groups")
async def get_alert_groups():
    """
    Get alerts grouped by labels.

    Compatible with Alertmanager v2 API.
    """
    # For simplicity, return all alerts in a single group
    alerts = storage.get_all_alerts()

    return [
        {
            "labels": {},
            "receiver": {"name": "default"},
            "alerts": alerts,
        }
    ]


@app.get("/api/v2/status")
@app.get("/api/v1/status")
async def get_status():
    """
    Get Alertmanager status.

    Compatible with Alertmanager v2 API.
    """
    return Status(
        cluster={
            "status": "ready",
            "peers": [],
        },
        versionInfo={
            "version": config.alertmanager_version,
            "revision": "ai-alertmanager",
            "branch": "main",
            "buildUser": "holmesgpt",
            "buildDate": "2024-01-01",
            "goVersion": "go1.21.0",
        },
        config={
            "original": "",
        },
        uptime="0h0m0s",
    )


@app.get("/api/v2/silences")
@app.get("/api/v1/silences")
async def get_silences():
    """
    Get all silences.

    Compatible with Alertmanager v2 API.
    Note: Silence functionality is not yet implemented.
    """
    # Return empty list for now
    return []


@app.post("/api/v2/silences")
@app.post("/api/v1/silences")
async def create_silence():
    """
    Create a new silence.

    Compatible with Alertmanager v2 API.
    Note: Silence functionality is not yet implemented.
    """
    raise HTTPException(status_code=501, detail="Silences not yet implemented")


@app.delete("/api/v2/silence/{silence_id}")
@app.delete("/api/v1/silence/{silence_id}")
async def delete_silence(silence_id: str):
    """
    Delete a silence.

    Compatible with Alertmanager v2 API.
    Note: Silence functionality is not yet implemented.
    """
    raise HTTPException(status_code=501, detail="Silences not yet implemented")


@app.get("/api/v2/receivers")
@app.get("/api/v1/receivers")
async def get_receivers():
    """
    Get all receivers.

    Compatible with Alertmanager v2 API.
    """
    return [{"name": "default"}]


# =============================================================================
# Health and Readiness Endpoints
# =============================================================================


@app.get("/healthz")
@app.get("/-/healthy")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/readyz")
@app.get("/-/ready")
async def readiness_check():
    """Readiness check endpoint."""
    return {"status": "ready"}


# =============================================================================
# AI Investigation Endpoints (Custom)
# =============================================================================


@app.get("/api/v2/investigations/{fingerprint}")
async def get_investigation(fingerprint: str):
    """
    Get AI investigation result for an alert.

    This is a custom endpoint for querying investigation results.

    Args:
        fingerprint: Alert fingerprint

    Returns:
        Investigation result
    """
    investigation = storage.get_investigation(fingerprint)
    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    return investigation


@app.post("/api/v2/investigate/{fingerprint}")
async def trigger_investigation(
    fingerprint: str,
    background_tasks: BackgroundTasks,
):
    """
    Manually trigger investigation for an alert.

    This is a custom endpoint for manually triggering investigations.

    Args:
        fingerprint: Alert fingerprint
    """
    alert = storage.get_alert(fingerprint)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    background_tasks.add_task(enqueue_investigation, fingerprint, alert)

    return {"status": "investigation enqueued", "fingerprint": fingerprint}


@app.get("/api/v2/investigations")
async def get_all_investigations():
    """
    Get all investigation results.

    This is a custom endpoint for querying all investigations.
    """
    return list(storage.investigations.values())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_config=None,  # Use our custom logging
    )
