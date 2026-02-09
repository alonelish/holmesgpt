from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.plugins.toolsets.grafana.toolset_grafana import (
    SearchInsideAllDashboards,
)


def _make_toolset_mock(url: str = "http://grafana.test:3000") -> MagicMock:
    toolset = MagicMock()
    toolset.grafana_config.url = url
    toolset.grafana_config.external_url = None
    toolset.grafana_config.api_key = "test-key"
    toolset.grafana_config.headers = None
    toolset.grafana_config.verify_ssl = True
    toolset.name = "grafana/dashboards"
    return toolset


def _dashboard_summary(uid: str, title: str) -> Dict[str, Any]:
    return {
        "uid": uid,
        "title": title,
        "url": f"/d/{uid}/{title.lower().replace(' ', '-')}",
        "folderTitle": "General",
    }


def _dashboard_detail(
    uid: str,
    panels: List[Dict[str, Any]],
    templating: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    dashboard: Dict[str, Any] = {"uid": uid, "panels": panels}
    if templating:
        dashboard["templating"] = templating
    return {"dashboard": dashboard}


def _success(data: Any) -> StructuredToolResult:
    return StructuredToolResult(
        status=StructuredToolResultStatus.SUCCESS,
        data=data,
        params={},
    )


class TestExtractMatchingPanels:
    def test_finds_metric_in_targets(self):
        dashboard = {
            "panels": [
                {
                    "title": "CPU Panel",
                    "targets": [
                        {"expr": "rate(container_cpu_usage_seconds_total[5m])"}
                    ],
                }
            ]
        }
        result = SearchInsideAllDashboards._extract_matching_panels(
            dashboard, "container_cpu_usage_seconds_total"
        )
        assert result == ["CPU Panel"]

    def test_no_match(self):
        dashboard = {
            "panels": [
                {
                    "title": "Memory Panel",
                    "targets": [{"expr": "node_memory_Active_bytes"}],
                }
            ]
        }
        result = SearchInsideAllDashboards._extract_matching_panels(
            dashboard, "container_cpu_usage_seconds_total"
        )
        assert result == []

    def test_case_insensitive(self):
        dashboard = {
            "panels": [
                {
                    "title": "Panel A",
                    "targets": [{"expr": "Container_CPU_Usage_Seconds_Total"}],
                }
            ]
        }
        result = SearchInsideAllDashboards._extract_matching_panels(
            dashboard, "container_cpu_usage_seconds_total"
        )
        assert result == ["Panel A"]

    def test_nested_panels_in_row(self):
        dashboard = {
            "panels": [
                {
                    "title": "Row",
                    "type": "row",
                    "panels": [
                        {
                            "title": "Nested Panel",
                            "targets": [
                                {"expr": "rate(my_metric_total[5m])"}
                            ],
                        }
                    ],
                }
            ]
        }
        result = SearchInsideAllDashboards._extract_matching_panels(
            dashboard, "my_metric_total"
        )
        assert result == ["Nested Panel"]

    def test_untitled_panel(self):
        dashboard = {
            "panels": [
                {
                    "targets": [{"expr": "up"}],
                }
            ]
        }
        result = SearchInsideAllDashboards._extract_matching_panels(dashboard, "up")
        assert result == ["<untitled>"]

    def test_empty_panels(self):
        result = SearchInsideAllDashboards._extract_matching_panels(
            {"panels": []}, "anything"
        )
        assert result == []

    def test_no_panels_key(self):
        result = SearchInsideAllDashboards._extract_matching_panels({}, "anything")
        assert result == []


class TestSearchTemplating:
    def test_finds_metric_in_templating(self):
        dashboard = {
            "templating": {
                "list": [
                    {
                        "name": "instance",
                        "query": "label_values(node_cpu_seconds_total, instance)",
                    }
                ]
            }
        }
        assert SearchInsideAllDashboards._search_templating(
            dashboard, "node_cpu_seconds_total"
        )

    def test_no_match_in_templating(self):
        dashboard = {
            "templating": {
                "list": [{"name": "namespace", "query": "label_values(namespace)"}]
            }
        }
        assert not SearchInsideAllDashboards._search_templating(
            dashboard, "node_cpu_seconds_total"
        )

    def test_no_templating_key(self):
        assert not SearchInsideAllDashboards._search_templating({}, "anything")


class TestSearchInsideAllDashboardsInvoke:
    @patch.object(SearchInsideAllDashboards, "_make_grafana_request")
    def test_finds_matching_dashboard(self, mock_request: MagicMock):
        toolset = _make_toolset_mock()
        tool = SearchInsideAllDashboards(toolset)

        summaries = [_dashboard_summary("uid1", "K8s Overview")]
        detail = _dashboard_detail(
            "uid1",
            [
                {
                    "title": "CPU Usage",
                    "targets": [
                        {"expr": "rate(container_cpu_usage_seconds_total[5m])"}
                    ],
                }
            ],
        )

        mock_request.side_effect = [
            _success(summaries),  # search API page 1
            _success(detail),  # dashboard detail
        ]

        result = tool._invoke(
            {"search_text": "container_cpu_usage_seconds_total"}, MagicMock()
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data is not None
        assert result.data["total_dashboards_searched"] == 1
        assert len(result.data["matching_dashboards"]) == 1
        assert result.data["matching_dashboards"][0]["uid"] == "uid1"
        assert result.data["matching_dashboards"][0]["matching_panels"] == ["CPU Usage"]

    @patch.object(SearchInsideAllDashboards, "_make_grafana_request")
    def test_no_matches(self, mock_request: MagicMock):
        toolset = _make_toolset_mock()
        tool = SearchInsideAllDashboards(toolset)

        summaries = [_dashboard_summary("uid1", "K8s Overview")]
        detail = _dashboard_detail(
            "uid1",
            [
                {
                    "title": "Memory",
                    "targets": [{"expr": "node_memory_Active_bytes"}],
                }
            ],
        )

        mock_request.side_effect = [
            _success(summaries),
            _success(detail),
        ]

        result = tool._invoke(
            {"search_text": "nonexistent_metric"}, MagicMock()
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data is not None
        assert result.data["total_dashboards_searched"] == 1
        assert len(result.data["matching_dashboards"]) == 0

    @patch.object(SearchInsideAllDashboards, "_make_grafana_request")
    def test_match_in_templating(self, mock_request: MagicMock):
        toolset = _make_toolset_mock()
        tool = SearchInsideAllDashboards(toolset)

        summaries = [_dashboard_summary("uid1", "Overview")]
        detail = _dashboard_detail(
            "uid1",
            [],
            templating={
                "list": [
                    {
                        "name": "instance",
                        "query": "label_values(node_cpu_seconds_total, instance)",
                    }
                ]
            },
        )

        mock_request.side_effect = [
            _success(summaries),
            _success(detail),
        ]

        result = tool._invoke(
            {"search_text": "node_cpu_seconds_total"}, MagicMock()
        )
        assert result.data is not None
        assert len(result.data["matching_dashboards"]) == 1
        assert result.data["matching_dashboards"][0]["found_in_templating_variables"] is True
        assert "matching_panels" not in result.data["matching_dashboards"][0]

    @patch.object(SearchInsideAllDashboards, "_make_grafana_request")
    def test_skips_dashboard_on_fetch_error(self, mock_request: MagicMock):
        toolset = _make_toolset_mock()
        tool = SearchInsideAllDashboards(toolset)

        summaries = [
            _dashboard_summary("uid1", "Good"),
            _dashboard_summary("uid2", "Broken"),
        ]
        detail_good = _dashboard_detail(
            "uid1",
            [{"title": "P1", "targets": [{"expr": "my_metric"}]}],
        )

        mock_request.side_effect = [
            _success(summaries),
            _success(detail_good),
            Exception("API Error"),  # uid2 fails
        ]

        result = tool._invoke({"search_text": "my_metric"}, MagicMock())
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data is not None
        assert result.data["total_dashboards_searched"] == 2
        assert len(result.data["matching_dashboards"]) == 1
        assert result.data["matching_dashboards"][0]["uid"] == "uid1"

    @patch.object(SearchInsideAllDashboards, "_make_grafana_request")
    def test_pagination(self, mock_request: MagicMock):
        toolset = _make_toolset_mock()
        tool = SearchInsideAllDashboards(toolset)
        # Simulate a small page size so we can test pagination
        tool._SEARCH_LIMIT_PER_PAGE = 1

        page1 = [_dashboard_summary("uid1", "Dash1")]
        page2 = [_dashboard_summary("uid2", "Dash2")]

        detail1 = _dashboard_detail(
            "uid1",
            [{"title": "P1", "targets": [{"expr": "target_metric"}]}],
        )
        detail2 = _dashboard_detail(
            "uid2",
            [{"title": "P2", "targets": [{"expr": "other_metric"}]}],
        )

        mock_request.side_effect = [
            _success(page1),   # search page 1
            _success(page2),   # search page 2
            _success([]),      # search page 3 (empty -> stop)
            _success(detail1),  # dashboard uid1
            _success(detail2),  # dashboard uid2
        ]

        result = tool._invoke({"search_text": "target_metric"}, MagicMock())
        assert result.data is not None
        assert result.data["total_dashboards_searched"] == 2
        assert len(result.data["matching_dashboards"]) == 1
        assert result.data["matching_dashboards"][0]["uid"] == "uid1"

    @patch.object(SearchInsideAllDashboards, "_make_grafana_request")
    def test_empty_grafana_instance(self, mock_request: MagicMock):
        toolset = _make_toolset_mock()
        tool = SearchInsideAllDashboards(toolset)

        mock_request.return_value = _success([])

        result = tool._invoke({"search_text": "anything"}, MagicMock())
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data is not None
        assert result.data["total_dashboards_searched"] == 0
        assert result.data["matching_dashboards"] == []
