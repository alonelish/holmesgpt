"""
Custom metrics exporter that simulates a sudden traffic drop to zero.

Scenario: A payment gateway service processes ~100 req/s of steady traffic.
After ~60 seconds, all traffic drops to 0 suddenly (simulating a deadlock,
upstream dependency failure, or network partition). The service is still running
(pod is healthy) but no requests are being processed.

The counter stops incrementing after the drop, which means rate() will show
the drop from ~100 to 0.
"""
import time
import math
from http.server import HTTPServer, BaseHTTPRequestHandler

START_TIME = time.time()
SCRAPE_COUNT = 0

# Normal traffic: ~100 req/s with some natural variation
NORMAL_RATE = 100.0
JITTER_AMPLITUDE = 8.0

# After this many scrapes, traffic drops to zero
DROP_AFTER_SCRAPE = 12  # 12 * 5s = 60s of normal traffic, then drop

# Cumulative counter (to simulate a real counter that stops incrementing)
CUMULATIVE_REQUESTS = {
    "success": 0.0,
    "error": 0.0,
}
CUMULATIVE_LATENCY_SUM = 0.0
CUMULATIVE_CONNECTIONS = 0.0
FROZEN = False  # Once traffic drops, counters freeze


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global SCRAPE_COUNT, FROZEN, CUMULATIVE_REQUESTS, CUMULATIVE_LATENCY_SUM, CUMULATIVE_CONNECTIONS

        if self.path == "/metrics":
            SCRAPE_COUNT += 1

            if SCRAPE_COUNT <= DROP_AFTER_SCRAPE and not FROZEN:
                # Normal traffic period - increment counters
                rate = NORMAL_RATE + JITTER_AMPLITUDE * math.sin(SCRAPE_COUNT * 0.5)
                requests_this_interval = rate * 5  # 5s per scrape

                CUMULATIVE_REQUESTS["success"] += requests_this_interval * 0.97
                CUMULATIVE_REQUESTS["error"] += requests_this_interval * 0.03
                CUMULATIVE_LATENCY_SUM += requests_this_interval * 0.045  # ~45ms avg
                CUMULATIVE_CONNECTIONS += requests_this_interval * 0.8  # connection reuse
            else:
                # Traffic has dropped - counters freeze (no new requests)
                FROZEN = True

            metrics = self._generate_metrics()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(metrics.encode())
        elif self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def _generate_metrics(self):
        lines = []

        # HTTP request counter
        lines.append("# HELP http_requests_total Total number of HTTP requests processed.")
        lines.append("# TYPE http_requests_total counter")
        lines.append(f'http_requests_total{{service="payment-gateway",status="2xx"}} {CUMULATIVE_REQUESTS["success"]:.1f}')
        lines.append(f'http_requests_total{{service="payment-gateway",status="5xx"}} {CUMULATIVE_REQUESTS["error"]:.1f}')

        # Active connections gauge - drops to 0 when frozen
        lines.append("# HELP active_connections Current number of active connections.")
        lines.append("# TYPE active_connections gauge")
        if FROZEN:
            lines.append('active_connections{service="payment-gateway"} 0')
        else:
            connections = 15 + 5 * math.sin(SCRAPE_COUNT * 0.3)
            lines.append(f'active_connections{{service="payment-gateway"}} {connections:.0f}')

        # Request duration histogram
        lines.append("# HELP http_request_duration_seconds Request latency histogram.")
        lines.append("# TYPE http_request_duration_seconds histogram")
        total_reqs = CUMULATIVE_REQUESTS["success"] + CUMULATIVE_REQUESTS["error"]
        for le in ["0.01", "0.025", "0.05", "0.1", "0.25", "0.5", "1.0", "+Inf"]:
            le_val = float(le) if le != "+Inf" else float("inf")
            if le_val >= 0.1:
                frac = 1.0
            elif le_val >= 0.05:
                frac = 0.9
            elif le_val >= 0.025:
                frac = 0.6
            else:
                frac = 0.2
            lines.append(f'http_request_duration_seconds_bucket{{service="payment-gateway",le="{le}"}} {total_reqs * frac:.1f}')
        lines.append(f'http_request_duration_seconds_sum{{service="payment-gateway"}} {CUMULATIVE_LATENCY_SUM:.2f}')
        lines.append(f'http_request_duration_seconds_count{{service="payment-gateway"}} {total_reqs:.1f}')

        # Thread/goroutine count - stays normal (service is running, just stuck)
        lines.append("# HELP process_threads Number of OS threads in the process.")
        lines.append("# TYPE process_threads gauge")
        if FROZEN:
            # Threads are stuck but count stays high (deadlock symptom)
            lines.append("process_threads 48")
        else:
            lines.append(f"process_threads {20 + int(3 * math.sin(SCRAPE_COUNT * 0.4))}")

        # Queue depth - grows after freeze (requests queue up but never process)
        lines.append("# HELP request_queue_depth Number of requests waiting in queue.")
        lines.append("# TYPE request_queue_depth gauge")
        if FROZEN:
            scrapes_since_freeze = SCRAPE_COUNT - DROP_AFTER_SCRAPE
            queue_depth = min(scrapes_since_freeze * 50, 500)  # Grows then caps
            lines.append(f"request_queue_depth {queue_depth}")
        else:
            lines.append(f"request_queue_depth {int(2 + math.sin(SCRAPE_COUNT * 0.6))}")

        lines.append("")
        lines.append("# HELP exporter_info Exporter metadata.")
        lines.append("# TYPE exporter_info gauge")
        lines.append(f"exporter_scrape_count {SCRAPE_COUNT}")

        return "\n".join(lines) + "\n"

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 9100), MetricsHandler)
    print("Payment gateway metrics exporter running on :9100", flush=True)
    server.serve_forever()
