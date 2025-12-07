#!/usr/bin/env python3
import os
import time
import json
import urllib.request
import urllib.error
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import random

# Set random seed for reproducible logs
random.seed(164)

LOKI_URL = "http://loki:3100/loki/api/v1/push"
BATCH_SIZE = 50
BATCH_INTERVAL = 2  # seconds


class LogBatcher:
    """Batches logs and sends them to Loki"""

    def __init__(self):
        self.batch = []
        self.lock = threading.Lock()
        self.last_flush = time.time()
        threading.Thread(target=self._flush_periodically, daemon=True).start()

    def add_log(self, timestamp, level, message, **kwargs):
        """Add a log entry to the batch"""
        if timestamp is None:
            timestamp = datetime.utcnow()

        # Convert timestamp to nanoseconds since epoch
        if isinstance(timestamp, datetime):
            ts_nano = str(int(timestamp.timestamp() * 1e9))
        else:
            ts_nano = str(timestamp)

        # Build log line
        log_data = {
            "level": level,
            "message": message,
            "service": "payment-service",
            **kwargs,
        }
        log_line = json.dumps(log_data)

        with self.lock:
            self.batch.append([ts_nano, log_line])
            if len(self.batch) >= BATCH_SIZE:
                self._flush()

    def _flush(self):
        """Send batch to Loki"""
        if not self.batch:
            return

        # Group logs by level
        streams_by_level = {}
        for ts, log_line in self.batch[:BATCH_SIZE]:
            try:
                log_data = json.loads(log_line)
                level = log_data.get("level", "INFO")
            except json.JSONDecodeError:
                level = "INFO"

            if level not in streams_by_level:
                streams_by_level[level] = []
            streams_by_level[level].append([ts, log_line])

        # Prepare Loki push payload with separate streams per level
        pod_name = os.environ.get("HOSTNAME", "payment-service")
        streams = []
        for level, values in streams_by_level.items():
            streams.append(
                {
                    "stream": {
                        "job": "payment-service",
                        "namespace": "app-164",
                        "pod_name": pod_name,  # Standard label that Holmes will recognize
                        "level": level,
                        "service": "payment-service",
                    },
                    "values": values,
                }
            )

        payload = {"streams": streams}

        # Clear the batch we're sending
        self.batch = self.batch[BATCH_SIZE:]

        # Send to Loki
        try:
            req = urllib.request.Request(
                LOKI_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            response = urllib.request.urlopen(req, timeout=10)
            if response.status == 204:
                total_logs = sum(len(s["values"]) for s in payload["streams"])
                print(
                    f"✓ Pushed {total_logs} logs to Loki ({len(payload['streams'])} streams)",
                    flush=True,
                )
        except Exception as e:
            print(f"✗ Failed to push logs to Loki: {e}", flush=True)
            # Don't stop on errors, continue trying
            pass

    def _flush_periodically(self):
        """Flush logs periodically"""
        while True:
            time.sleep(BATCH_INTERVAL)
            with self.lock:
                if self.batch and time.time() - self.last_flush > BATCH_INTERVAL:
                    self._flush()
                    self.last_flush = time.time()

    def flush_all(self):
        """Force flush all remaining logs"""
        with self.lock:
            while self.batch:
                self._flush()


# Global log batcher
log_batcher = LogBatcher()


def log_structured(level, message, timestamp=None, **kwargs):
    """Log in structured format and send to Loki"""
    log_batcher.add_log(timestamp, level, message, **kwargs)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            response = {"status": "healthy"}
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()


def main():
    print("Payment service started - generating logs...", flush=True)

    # Add startup logs and flush immediately
    log_structured("INFO", "Payment service started", port=8080, version="1.0.0")
    log_structured(
        "INFO",
        "Initializing database connection pool",
        pool_size=20,
        min_connections=5,
        max_connections=50,
    )
    # Generate some initial error logs to ensure they're available
    log_structured(
        "ERROR",
        "Payment service started - initial error log for testing",
        error_code="STARTUP-001",
        transaction_id="TXN-STARTUP",
    )
    log_batcher.flush_all()  # Force immediate flush
    print("Initial logs flushed to Loki", flush=True)

    # Generate logs with errors
    def generate_logs():
        log_count = 0
        while True:
            log_type = random.random()
            if log_type < 0.4:
                # Normal info logs
                log_structured(
                    "INFO",
                    "Payment processed successfully",
                    payment_id=f"PAY-{random.randint(1000, 9999)}",
                    amount=round(random.uniform(10, 1000), 2),
                    currency="USD",
                    processing_time_ms=random.randint(100, 500),
                )
            elif log_type < 0.6:
                # Debug logs
                log_structured(
                    "DEBUG",
                    "Database query executed",
                    query="SELECT * FROM payments WHERE user_id = ?",
                    duration_ms=random.randint(5, 50),
                    rows_returned=random.randint(0, 10),
                )
            elif log_type < 0.75:
                # Warning logs
                log_structured(
                    "WARN",
                    "High latency detected",
                    endpoint="/api/payments",
                    latency_ms=random.randint(1000, 3000),
                )
            else:
                # ERROR logs - these are what we want to investigate
                error_messages = [
                    "Failed to process payment - database connection timeout",
                    "Payment validation failed - invalid card number",
                    "External API call failed - service unavailable",
                    "Transaction rollback failed - data inconsistency",
                    "Rate limit exceeded - too many requests",
                ]
                log_structured(
                    "ERROR",
                    random.choice(error_messages),
                    error_code=f"ERR-{random.randint(100, 999)}",
                    transaction_id=f"TXN-{random.randint(10000, 99999)}",
                    stack_trace="at PaymentService.processPayment()\n  at DatabasePool.acquire()",
                )

            log_count += 1
            if log_count % 20 == 0:
                log_batcher.flush_all()
                print(f"Generated {log_count} logs so far...", flush=True)

            time.sleep(random.uniform(2, 5))

    # Start log generation in background
    log_thread = threading.Thread(target=generate_logs, daemon=True)
    log_thread.start()

    # Start health endpoint server
    server = HTTPServer(("0.0.0.0", 8080), HealthHandler)
    print("HTTP server started on port 8080", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fatal error: {e}", flush=True)
        import traceback

        traceback.print_exc()
        raise
