#!/usr/bin/env python3
"""Generate a large Grafana dashboard JSON for testing panel patch functionality."""

import json

def generate_large_dashboard(num_panels: int = 100) -> dict:
    """Generate a dashboard with many panels to test context window limits."""
    
    panels = []
    
    for i in range(1, num_panels + 1):
        # Calculate grid position (6 panels per row, each 4 units wide)
        row = (i - 1) // 6
        col = (i - 1) % 6
        
        panel = {
            "id": i,
            "title": f"Panel {i}: {'Memory Pressure' if i == 42 else f'Metric {i}'}",
            "type": "timeseries" if i % 3 != 0 else "stat",
            "gridPos": {
                "x": col * 4,
                "y": row * 8,
                "w": 4,
                "h": 8
            },
            "targets": [
                {
                    "expr": f"{'node_memory_Buffers_bytes' if i == 42 else f'some_metric_{i}'} {{instance=~\".*\"}}",
                    "legendFormat": f"{{{{instance}}}} - Metric {i}",
                    "refId": "A"
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "unit": "bytes" if i % 2 == 0 else "percent",
                    "thresholds": {
                        "mode": "percentage",
                        "steps": [
                            {"color": "green", "value": None},
                            {"color": "yellow", "value": 70},
                            {"color": "red", "value": 90}
                        ]
                    }
                },
                "overrides": []
            },
            "options": {
                "legend": {"displayMode": "list", "placement": "bottom"},
                "tooltip": {"mode": "single"}
            },
            "description": f"This panel shows metric {i} data from the infrastructure monitoring system. " * 3
        }
        panels.append(panel)
    
    dashboard = {
        "dashboard": {
            "uid": "infra-monitoring-dash",
            "title": "Infrastructure Monitoring",
            "description": "A comprehensive dashboard for monitoring infrastructure metrics including CPU, memory, disk, and network usage across all nodes in the cluster.",
            "tags": ["infrastructure", "monitoring", "kubernetes", "prometheus"],
            "panels": panels,
            "schemaVersion": 39,
            "timezone": "browser",
            "time": {
                "from": "now-6h",
                "to": "now"
            },
            "refresh": "30s",
            "templating": {
                "list": [
                    {
                        "name": "instance",
                        "type": "query",
                        "datasource": "Prometheus",
                        "query": "label_values(up, instance)",
                        "refresh": 1,
                        "multi": True,
                        "includeAll": True
                    }
                ]
            }
        },
        "overwrite": True
    }
    
    return dashboard

if __name__ == "__main__":
    dashboard = generate_large_dashboard(100)
    print(json.dumps(dashboard, indent=2))
