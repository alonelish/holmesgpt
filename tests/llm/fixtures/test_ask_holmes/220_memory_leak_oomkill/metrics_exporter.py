"""
Metrics exporter that simulates container memory metrics for the model-cache service.

This exporter simulates what you'd see from cAdvisor / kubelet metrics:
- container_memory_working_set_bytes increases monotonically over time
- container_memory_usage_bytes tracks slightly above working set
- The pattern clearly shows a memory leak (steady linear increase)

We also expose metrics for two other pods (healthy ones) that have stable memory,
so Holmes can compare and identify the leaking pod specifically.
"""
import time
import math
from http.server import HTTPServer, BaseHTTPRequestHandler

START_TIME = time.time()
SCRAPE_COUNT = 0

# Memory limit for model-cache: 256Mi = 268435456 bytes
MEMORY_LIMIT = 268435456

# model-cache: starts at 40MB, grows ~3MB per scrape (5s interval = ~36MB/min)
# Will reach 256MB in about 72 scrapes = 360s = 6 minutes
MODEL_CACHE_BASE_MB = 40
MODEL_CACHE_GROWTH_PER_SCRAPE_MB = 3.0

# Other stable pods for comparison
STABLE_PODS = {
    "auth-service": {"base_mb": 85, "jitter_mb": 3},
    "rate-limiter": {"base_mb": 45, "jitter_mb": 2},
}


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

        # Container memory working set (the key metric for OOM detection)
        lines.append("# HELP container_memory_working_set_bytes Current working set of the container in bytes.")
        lines.append("# TYPE container_memory_working_set_bytes gauge")

        # model-cache: monotonically increasing memory
        mc_memory_mb = MODEL_CACHE_BASE_MB + (SCRAPE_COUNT * MODEL_CACHE_GROWTH_PER_SCRAPE_MB)
        mc_memory_mb = min(mc_memory_mb, MEMORY_LIMIT / (1024 * 1024))  # Cap at limit
        mc_memory_bytes = mc_memory_mb * 1024 * 1024

        lines.append(
            f'container_memory_working_set_bytes{{pod="model-cache-7f8d9b4c6-xk2pq",container="model-cache",namespace="app-220"}} {mc_memory_bytes:.0f}'
        )

        # Stable pods
        for pod_name, cfg in STABLE_PODS.items():
            jitter = cfg["jitter_mb"] * math.sin(SCRAPE_COUNT * 0.3 + hash(pod_name) % 10)
            mem_mb = cfg["base_mb"] + jitter
            mem_bytes = mem_mb * 1024 * 1024
            pod_suffix = "a3b2c1" if pod_name == "auth-service" else "d4e5f6"
            lines.append(
                f'container_memory_working_set_bytes{{pod="{pod_name}-5c9d8e7f-{pod_suffix}",container="{pod_name}",namespace="app-220"}} {mem_bytes:.0f}'
            )

        # Container memory limit
        lines.append("# HELP container_spec_memory_limit_bytes Memory limit for the container.")
        lines.append("# TYPE container_spec_memory_limit_bytes gauge")
        lines.append(
            f'container_spec_memory_limit_bytes{{pod="model-cache-7f8d9b4c6-xk2pq",container="model-cache",namespace="app-220"}} {MEMORY_LIMIT}'
        )
        for pod_name in STABLE_PODS:
            pod_suffix = "a3b2c1" if pod_name == "auth-service" else "d4e5f6"
            lines.append(
                f'container_spec_memory_limit_bytes{{pod="{pod_name}-5c9d8e7f-{pod_suffix}",container="{pod_name}",namespace="app-220"}} 536870912'
            )

        # Memory usage percentage (derived)
        lines.append("# HELP container_memory_usage_bytes Total memory usage of the container.")
        lines.append("# TYPE container_memory_usage_bytes gauge")
        # Usage is slightly higher than working set (includes caches)
        mc_usage_bytes = mc_memory_bytes * 1.05
        lines.append(
            f'container_memory_usage_bytes{{pod="model-cache-7f8d9b4c6-xk2pq",container="model-cache",namespace="app-220"}} {mc_usage_bytes:.0f}'
        )

        # RSS memory
        lines.append("# HELP container_memory_rss Resident set size of the container.")
        lines.append("# TYPE container_memory_rss gauge")
        lines.append(
            f'container_memory_rss{{pod="model-cache-7f8d9b4c6-xk2pq",container="model-cache",namespace="app-220"}} {mc_memory_bytes * 0.95:.0f}'
        )

        # Process count (stable - service isn't forking/spawning)
        lines.append("# HELP container_processes Number of processes in the container.")
        lines.append("# TYPE container_processes gauge")
        lines.append('container_processes{pod="model-cache-7f8d9b4c6-xk2pq",container="model-cache",namespace="app-220"} 1')

        # Scrape metadata
        lines.append("# HELP exporter_scrape_count Number of scrapes.")
        lines.append("# TYPE exporter_scrape_count counter")
        lines.append(f"exporter_scrape_count {SCRAPE_COUNT}")

        return "\n".join(lines) + "\n"

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 9220), MetricsHandler)
    print("Container metrics exporter running on :9220", flush=True)
    server.serve_forever()
