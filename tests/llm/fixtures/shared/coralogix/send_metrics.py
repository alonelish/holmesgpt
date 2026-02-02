#!/usr/bin/env python3
"""
Send test metrics to Coralogix via Prometheus RemoteWrite.

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
import sys
import time
import struct
import snappy
import requests


def encode_varint(value):
    """Encode an integer as a varint."""
    bits = value & 0x7f
    value >>= 7
    result = b""
    while value:
        result += bytes([0x80 | bits])
        bits = value & 0x7f
        value >>= 7
    result += bytes([bits])
    return result


def encode_string(field_num, s):
    """Encode a string field in protobuf format."""
    encoded = s.encode('utf-8')
    return bytes([field_num << 3 | 2]) + encode_varint(len(encoded)) + encoded


def encode_double(field_num, value):
    """Encode a double field in protobuf format."""
    return bytes([field_num << 3 | 1]) + struct.pack('<d', value)


def encode_int64(field_num, value):
    """Encode an int64 field in protobuf format."""
    return bytes([field_num << 3 | 0]) + encode_varint(value)


def encode_label(name, value):
    """Encode a Label message: name=1, value=2."""
    content = encode_string(1, name) + encode_string(2, value)
    return bytes([1 << 3 | 2]) + encode_varint(len(content)) + content  # field 1 = labels


def encode_sample(value, timestamp_ms):
    """Encode a Sample message: value=1 (double), timestamp=2 (int64)."""
    content = encode_double(1, value) + encode_int64(2, timestamp_ms)
    return bytes([2 << 3 | 2]) + encode_varint(len(content)) + content  # field 2 = samples


def encode_timeseries(labels, samples):
    """Encode a TimeSeries message."""
    content = b""
    for name, value in labels:
        content += encode_label(name, value)
    for value, timestamp_ms in samples:
        content += encode_sample(value, timestamp_ms)
    return bytes([1 << 3 | 2]) + encode_varint(len(content)) + content  # field 1 = timeseries


def send_metrics(domain: str, api_key: str, app_name: str, subsystem: str,
                 metric_prefix: str) -> bool:
    """Send test metrics to Coralogix via Prometheus RemoteWrite."""

    endpoint = f"https://ingress.{domain}/prometheus/v1"
    timestamp_ms = int(time.time() * 1000)

    # Create metric data points
    timeseries_data = []

    # HTTP requests total by endpoint
    endpoints = ["/api/checkout", "/api/cart", "/api/products", "/api/users"]
    for endpoint_path in endpoints:
        labels = [
            ("__name__", f"{metric_prefix}_http_requests_total"),
            ("endpoint", endpoint_path),
            ("status_code", "200"),
            ("app", app_name),
        ]
        count = {"checkout": 150, "cart": 89, "products": 230, "users": 45}.get(endpoint_path.split("/")[-1], 100)
        timeseries_data.append((labels, [(float(count), timestamp_ms)]))

    # HTTP errors by endpoint
    error_endpoints = ["/api/checkout", "/api/cart"]
    for endpoint_path in error_endpoints:
        for status_code in ["500", "503"]:
            labels = [
                ("__name__", f"{metric_prefix}_http_errors_total"),
                ("endpoint", endpoint_path),
                ("status_code", status_code),
                ("app", app_name),
            ]
            count = 12 if status_code == "500" else 5
            timeseries_data.append((labels, [(float(count), timestamp_ms)]))

    # Request latency
    labels = [
        ("__name__", f"{metric_prefix}_request_latency_seconds"),
        ("endpoint", "/api/checkout"),
        ("quantile", "0.95"),
        ("app", app_name),
    ]
    timeseries_data.append((labels, [(0.45, timestamp_ms)]))

    # Encode WriteRequest protobuf
    write_request = b""
    for labels, samples in timeseries_data:
        write_request += encode_timeseries(labels, samples)

    # Compress with snappy
    compressed = snappy.compress(write_request)

    print(f"Sending {len(timeseries_data)} metric series to {endpoint}...")

    # Send via Prometheus RemoteWrite
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/x-protobuf",
        "Content-Encoding": "snappy",
        "X-Prometheus-Remote-Write-Version": "0.1.0",
        "CX-Application-Name": app_name,
        "CX-Subsystem-Name": subsystem,
    }

    try:
        response = requests.post(endpoint, data=compressed, headers=headers, verify=False, timeout=30)
        response.raise_for_status()
        print(f"✅ Metrics sent successfully with prefix={metric_prefix}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to send metrics: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response: {e.response.text}")
        return False


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

    # Suppress SSL warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
