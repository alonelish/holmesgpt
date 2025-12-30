import json


def build_panel(panel_id: int, row: int, col: int) -> dict:
    return {
        "id": panel_id,
        "title": f"Panel {panel_id} - Requests {row}/{col}",
        "type": "timeseries",
        "gridPos": {"h": 4, "w": 6, "x": (col % 4) * 6, "y": row * 4},
        "targets": [
            {"refId": "A", "expr": f"rate(requests_total{{service=\"svc-{panel_id}\"}}[5m])"},
            {"refId": "B", "expr": f"histogram_quantile(0.95, rate(latency_bucket{{service=\"svc-{panel_id}\"}}[5m]))"},
        ],
        "options": {
            "tooltip": {"mode": "multi", "sort": "desc"},
            "legend": {"displayMode": "list", "placement": "right"},
        },
        "fieldConfig": {
            "defaults": {
                "unit": "reqps",
                "custom": {
                    "drawStyle": "line",
                    "lineWidth": 1,
                    "showPoints": "auto",
                },
            }
        },
    }


def build_dashboard(panel_count: int = 40) -> dict:
    panels = []
    for i in range(panel_count):
        row = i // 4
        col = i % 4
        panels.append(build_panel(i + 1, row, col))

    # Add a nested panel structure to force deeper JSON
    nested_panel = {
        "id": panel_count + 1,
        "title": "Nested Summary",
        "type": "row",
        "gridPos": {"h": 2, "w": 24, "x": 0, "y": (panel_count // 4 + 1) * 4},
        "panels": [
            {
                "id": panel_count + 2,
                "title": "Error Overview",
                "type": "stat",
                "gridPos": {"h": 4, "w": 6, "x": 0, "y": 0},
                "targets": [{"refId": "A", "expr": "rate(errors_total[5m])"}],
                "fieldConfig": {"defaults": {"unit": "ops"}},
            },
            {
                "id": panel_count + 3,
                "title": "Cache Hit Rate",
                "type": "gauge",
                "gridPos": {"h": 4, "w": 6, "x": 6, "y": 0},
                "targets": [{"refId": "A", "expr": "avg_over_time(cache_hit_rate[5m])"}],
                "fieldConfig": {"defaults": {"unit": "percent"}},
            },
        ],
    }
    panels.append(nested_panel)

    return {
        "dashboard": {
            "id": None,
            "uid": "filterdemo",
            "title": "Filter Demo Dashboard",
            "schemaVersion": 36,
            "version": 1,
            "editable": False,
            "panels": panels,
            "tags": ["filters", "depth-control", "jq-jsonpath"],
            "templating": {"list": []},
        },
        "overwrite": True,
    }


if __name__ == "__main__":
    print(json.dumps(build_dashboard(), indent=2))
