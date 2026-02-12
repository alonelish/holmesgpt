"""
Metrics exporter simulating node-level and per-process file descriptor metrics.

Scenario: A node is running out of file descriptors. The node's total allocated
FDs are climbing toward the system limit. Among several pods on the node, one
specific pod (log-collector) is leaking file descriptors - it opens log files
but never closes them. Other pods maintain stable FD usage.

Holmes needs to:
1. Identify the node is running low on FDs
2. Look at per-pod/process FD metrics
3. Identify log-collector as the pod leaking FDs
"""
import time
import math
from http.server import HTTPServer, BaseHTTPRequestHandler

SCRAPE_COUNT = 0

# Node-level file descriptor config
NODE_FD_MAX = 65536  # System limit (fs.file-max)
NODE_FD_BASE = 8500  # Baseline allocated FDs from system services

# Pod FD profiles
PODS = {
    "log-collector-6b8f4d9a7-mv2rq": {
        "container": "log-collector",
        "base_fds": 45,
        "leak_per_scrape": 12,  # Opens ~12 FDs per 5s interval and never closes them
        "stable": False,
    },
    "event-router-5c7e3b2d1-nt8pw": {
        "container": "event-router",
        "base_fds": 120,
        "jitter": 8,
        "stable": True,
    },
    "metrics-aggregator-4a6d2c1e9-qr5km": {
        "container": "metrics-aggregator",
        "base_fds": 85,
        "jitter": 5,
        "stable": True,
    },
    "config-watcher-3f9a8b7c2-js4hn": {
        "container": "config-watcher",
        "base_fds": 30,
        "jitter": 3,
        "stable": True,
    },
    "cache-proxy-2e5d1c4f8-wp6tl": {
        "container": "cache-proxy",
        "base_fds": 200,
        "jitter": 15,
        "stable": True,
    },
}


def get_pod_fds(pod_name, cfg, scrape):
    """Calculate current FD count for a pod."""
    if not cfg["stable"]:
        # Leaking pod: FDs increase monotonically
        return cfg["base_fds"] + (scrape * cfg["leak_per_scrape"])
    else:
        # Stable pod: fluctuates around base
        jitter = cfg["jitter"] * math.sin(scrape * 0.4 + hash(pod_name) % 7)
        return cfg["base_fds"] + jitter


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

        # Node-level file descriptor metrics
        total_pod_fds = sum(
            get_pod_fds(name, cfg, SCRAPE_COUNT) for name, cfg in PODS.items()
        )
        node_allocated_fds = NODE_FD_BASE + total_pod_fds

        lines.append("# HELP node_filefd_allocated Number of allocated file descriptors on the node.")
        lines.append("# TYPE node_filefd_allocated gauge")
        lines.append(f'node_filefd_allocated{{node="worker-node-3"}} {node_allocated_fds:.0f}')

        lines.append("# HELP node_filefd_maximum Maximum number of file descriptors allowed on the node.")
        lines.append("# TYPE node_filefd_maximum gauge")
        lines.append(f'node_filefd_maximum{{node="worker-node-3"}} {NODE_FD_MAX}')

        # Node FD usage percentage (derived metric for convenience)
        fd_usage_pct = (node_allocated_fds / NODE_FD_MAX) * 100
        lines.append("# HELP node_filefd_usage_percent Percentage of file descriptors in use on the node.")
        lines.append("# TYPE node_filefd_usage_percent gauge")
        lines.append(f'node_filefd_usage_percent{{node="worker-node-3"}} {fd_usage_pct:.1f}')

        # Per-pod/process file descriptor metrics
        lines.append("# HELP process_open_fds Number of open file descriptors per pod/process.")
        lines.append("# TYPE process_open_fds gauge")

        for pod_name, cfg in PODS.items():
            fds = get_pod_fds(pod_name, cfg, SCRAPE_COUNT)
            lines.append(
                f'process_open_fds{{pod="{pod_name}",container="{cfg["container"]}",namespace="app-221"}} {fds:.0f}'
            )

        # Per-pod FD limit
        lines.append("# HELP process_max_fds Maximum number of open file descriptors per process.")
        lines.append("# TYPE process_max_fds gauge")
        for pod_name, cfg in PODS.items():
            lines.append(
                f'process_max_fds{{pod="{pod_name}",container="{cfg["container"]}",namespace="app-221"}} 1048576'
            )

        # Per-pod network socket counts (stable for all pods - not a socket leak)
        lines.append("# HELP process_network_sockets Number of network sockets per pod.")
        lines.append("# TYPE process_network_sockets gauge")
        for pod_name, cfg in PODS.items():
            if cfg["container"] == "cache-proxy":
                sockets = 45 + int(5 * math.sin(SCRAPE_COUNT * 0.3))
            elif cfg["container"] == "event-router":
                sockets = 20 + int(3 * math.sin(SCRAPE_COUNT * 0.5))
            else:
                sockets = 8 + int(2 * math.sin(SCRAPE_COUNT * 0.4))
            lines.append(
                f'process_network_sockets{{pod="{pod_name}",container="{cfg["container"]}",namespace="app-221"}} {sockets}'
            )

        # Exporter metadata
        lines.append("# HELP exporter_scrape_count Number of scrapes performed.")
        lines.append("# TYPE exporter_scrape_count counter")
        lines.append(f"exporter_scrape_count {SCRAPE_COUNT}")

        return "\n".join(lines) + "\n"

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 9221), MetricsHandler)
    print("Node metrics exporter running on :9221", flush=True)
    server.serve_forever()
