#!/usr/bin/env python3
"""
Send test metrics to Coralogix via OTLP gRPC.

Usage:
    python send_metrics.py --domain eu2.coralogix.com --api-key <key> \
        --app-name holmes-eval-175 --subsystem metrics-test \
        --metric-prefix eval175

Environment variables (alternative to CLI args):
    Domain defaults to eu2.coralogix.com
    CORALOGIX_SEND_API_KEY - API key with SendData permissions (for ingestion)

Note: Coralogix uses separate API keys for sending vs querying data.
See: https://coralogix.com/docs/user-guides/account-management/api-keys/api-keys/
"""

import argparse
import os
import random
import sys
import time


def send_metrics(domain: str, api_key: str, app_name: str, subsystem: str,
                 metric_prefix: str) -> bool:
    """Send test metrics to Coralogix via OTLP gRPC."""
    try:
        from opentelemetry import metrics
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
    except ImportError:
        print("ERROR: OpenTelemetry packages not installed. Run:")
        print("  pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc")
        return False

    # Configure resource with Coralogix-specific attributes
    resource = Resource.create({
        "service.name": app_name,
        "cx.application.name": app_name,
        "cx.subsystem.name": subsystem,
    })

    # Configure OTLP exporter for Coralogix
    # Note: gRPC metadata keys must be lowercase
    endpoint = f"ingress.{domain}:443"
    exporter = OTLPMetricExporter(
        endpoint=endpoint,
        headers={
            "authorization": f"Bearer {api_key}",
            "cx-application-name": app_name,
            "cx-subsystem-name": subsystem,
        },
    )

    # Use a short export interval for faster test setup
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=1000)
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)

    meter = metrics.get_meter(__name__)

    print(f"Sending metrics to {endpoint}...")

    # Create metrics with the specified prefix
    request_counter = meter.create_counter(
        f"{metric_prefix}_http_requests_total",
        description="Total HTTP requests",
        unit="1",
    )

    error_counter = meter.create_counter(
        f"{metric_prefix}_http_errors_total",
        description="Total HTTP errors",
        unit="1",
    )

    latency_histogram = meter.create_histogram(
        f"{metric_prefix}_request_latency_seconds",
        description="Request latency in seconds",
        unit="s",
    )

    # Generate some metric data points
    endpoints = ["/api/checkout", "/api/cart", "/api/products", "/api/users"]
    status_codes = ["200", "201", "400", "500", "503"]

    print("Generating metric data points...")
    for _ in range(50):
        api_endpoint = random.choice(endpoints)
        status = random.choice(status_codes)
        latency = random.uniform(0.01, 2.0)

        attributes = {"endpoint": api_endpoint, "status_code": status, "app": app_name}

        request_counter.add(1, attributes)
        latency_histogram.record(latency, attributes)

        if status in ["500", "503"]:
            error_counter.add(1, attributes)

        time.sleep(0.02)

    # Force flush to ensure metrics are sent
    print("Flushing metrics...")
    provider.force_flush()

    # Give some time for final export
    time.sleep(3)
    provider.shutdown()

    print(f"✅ Metrics sent successfully with prefix={metric_prefix}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Send test metrics to Coralogix")
    parser.add_argument("--domain", default=os.environ.get("CORALOGIX_DOMAIN", "eu2.coralogix.com"),
                        help="Coralogix domain (e.g., eu2.coralogix.com)")
    parser.add_argument("--api-key", default=os.environ.get("CORALOGIX_SEND_API_KEY"),
                        help="Coralogix Send-Your-Data API key (SendData permissions)")
    parser.add_argument("--app-name", required=True, help="Application name")
    parser.add_argument("--subsystem", required=True, help="Subsystem name")
    parser.add_argument("--metric-prefix", required=True, help="Prefix for metric names")

    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: --api-key or CORALOGIX_SEND_API_KEY required")
        sys.exit(1)

    success = send_metrics(
        domain=args.domain,
        api_key=args.api_key,
        app_name=args.app_name,
        subsystem=args.subsystem,
        metric_prefix=args.metric_prefix,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
