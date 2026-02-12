"""
Custom metrics exporter that simulates HTTP request traffic patterns.

Scenario: A web API receives traffic from multiple countries. After an initial
period of normal traffic, there is a sudden spike in requests from Brazil (BR)
while other countries remain at their normal levels. This simulates a scenario
where a bot network or viral social media post causes a traffic surge from a
specific geographic region.

The exporter changes metric values every scrape to build a realistic time series.
"""
import time
import math
from http.server import HTTPServer, BaseHTTPRequestHandler

START_TIME = time.time()
SCRAPE_COUNT = 0

# Traffic configuration per country (requests/sec baseline)
COUNTRIES = {
    "US": {"baseline": 45, "jitter": 5},
    "DE": {"baseline": 20, "jitter": 3},
    "JP": {"baseline": 15, "jitter": 2},
    "BR": {"baseline": 12, "jitter": 2},  # Will spike
    "GB": {"baseline": 10, "jitter": 2},
}

ENDPOINTS = ["/api/search", "/api/catalog", "/api/checkout"]
STATUS_CODES = ["200", "201", "400", "500"]
STATUS_WEIGHTS = [0.85, 0.08, 0.05, 0.02]

# Spike config: BR spikes after ~60s of normal traffic
SPIKE_START_SCRAPE = 12  # After 12 scrapes (60s at 5s interval)
BR_SPIKE_MULTIPLIER = 25  # 12 * 25 = 300 req/s from BR during spike


def get_request_rate(country, scrape_num):
    """Calculate current request rate for a country at this scrape."""
    cfg = COUNTRIES[country]
    base = cfg["baseline"]
    jitter = cfg["jitter"] * math.sin(scrape_num * 0.7 + hash(country) % 10)

    if country == "BR" and scrape_num >= SPIKE_START_SCRAPE:
        # Sharp spike for Brazil
        return base * BR_SPIKE_MULTIPLIER + jitter * 3
    return base + jitter


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global SCRAPE_COUNT

        if self.path == "/metrics":
            SCRAPE_COUNT += 1
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
        lines.append("# HELP http_requests_total Total HTTP requests by country, endpoint, and status.")
        lines.append("# TYPE http_requests_total counter")

        # We accumulate counters to simulate a real counter metric
        elapsed = SCRAPE_COUNT * 5  # 5s scrape interval

        for country, cfg in COUNTRIES.items():
            # Calculate cumulative requests up to this point
            cumulative = 0.0
            for s in range(1, SCRAPE_COUNT + 1):
                rate = get_request_rate(country, s)
                cumulative += rate * 5  # 5s per scrape interval

            # Distribute across endpoints and status codes
            for endpoint in ENDPOINTS:
                ep_share = {"/api/search": 0.5, "/api/catalog": 0.3, "/api/checkout": 0.2}[endpoint]
                for code, weight in zip(STATUS_CODES, STATUS_WEIGHTS):
                    value = cumulative * ep_share * weight
                    lines.append(
                        f'http_requests_total{{country="{country}",endpoint="{endpoint}",status_code="{code}",service="api-gateway",namespace="app-218"}} {value:.1f}'
                    )

        lines.append("")
        lines.append("# HELP http_request_duration_seconds HTTP request latency histogram.")
        lines.append("# TYPE http_request_duration_seconds histogram")

        # Add latency histogram (simpler - just summary stats)
        for country in COUNTRIES:
            rate = get_request_rate(country, SCRAPE_COUNT)
            # Higher traffic = slightly higher latency
            avg_latency = 0.05 + (rate / 5000.0)
            count = 0.0
            for s in range(1, SCRAPE_COUNT + 1):
                count += get_request_rate(country, s) * 5

            for le in ["0.01", "0.025", "0.05", "0.1", "0.25", "0.5", "1.0", "+Inf"]:
                le_val = float(le) if le != "+Inf" else float("inf")
                # Simple CDF approximation
                if le_val >= avg_latency * 3:
                    bucket_frac = 1.0
                elif le_val >= avg_latency:
                    bucket_frac = 0.85
                elif le_val >= avg_latency * 0.5:
                    bucket_frac = 0.5
                else:
                    bucket_frac = 0.1
                lines.append(
                    f'http_request_duration_seconds_bucket{{country="{country}",le="{le}",service="api-gateway",namespace="app-218"}} {count * bucket_frac:.1f}'
                )
            lines.append(f'http_request_duration_seconds_sum{{country="{country}",service="api-gateway",namespace="app-218"}} {count * avg_latency:.2f}')
            lines.append(f'http_request_duration_seconds_count{{country="{country}",service="api-gateway",namespace="app-218"}} {count:.1f}')

        lines.append("")
        lines.append("# HELP exporter_scrape_count Number of times this exporter has been scraped.")
        lines.append("# TYPE exporter_scrape_count counter")
        lines.append(f"exporter_scrape_count {SCRAPE_COUNT}")

        return "\n".join(lines) + "\n"

    def log_message(self, format, *args):
        pass  # Suppress request logging


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 9218), MetricsHandler)
    print("Metrics exporter running on :9218", flush=True)
    server.serve_forever()
