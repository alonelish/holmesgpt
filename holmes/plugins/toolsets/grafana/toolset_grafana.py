import json
import logging
import os
from abc import ABC
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type, cast
from urllib.parse import urlencode, urljoin

import requests  # type: ignore

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.plugins.toolsets.grafana.base_grafana_toolset import BaseGrafanaToolset
from holmes.plugins.toolsets.grafana.common import (
    GrafanaConfig,
    build_headers,
    get_base_url,
)
from holmes.plugins.toolsets.json_filter_mixin import JsonFilterMixin
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

logger = logging.getLogger(__name__)


class GrafanaDashboardConfig(GrafanaConfig):
    """Configuration specific to Grafana Dashboard toolset."""

    pass


def _build_grafana_dashboard_url(
    config: GrafanaDashboardConfig,
    uid: Optional[str] = None,
    query_params: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    try:
        base_url = config.external_url or config.url
        if uid:
            return f"{base_url.rstrip('/')}/d/{uid}"
        else:
            query_string = urlencode(query_params, doseq=True) if query_params else ""
            if query_string:
                return f"{base_url.rstrip('/')}/dashboards?{query_string}"
            else:
                return f"{base_url.rstrip('/')}/dashboards"
    except Exception:
        return None


class GrafanaToolset(BaseGrafanaToolset):
    config_class: ClassVar[Type[GrafanaDashboardConfig]] = GrafanaDashboardConfig

    def __init__(self):
        super().__init__(
            name="grafana/dashboards",
            description="Provides tools for interacting with Grafana dashboards",
            icon_url="https://w7.pngwing.com/pngs/434/923/png-transparent-grafana-hd-logo-thumbnail.png",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/grafanadashboards/",
            tools=[
                SearchDashboards(self),
                SearchInsideAllDashboards(self),
                GetDashboardByUID(self),
                GetHomeDashboard(self),
                GetDashboardTags(self),
            ],
        )

        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "toolset_grafana_dashboard.jinja2"
        )

    def health_check(self) -> Tuple[bool, str]:
        """Test connectivity by invoking GetDashboardTags tool."""
        tool = GetDashboardTags(self)
        try:
            _ = tool._make_grafana_request("api/dashboards/tags", {})
            return True, ""
        except Exception as e:
            return False, f"Failed to connect to Grafana {str(e)}"

    @property
    def grafana_config(self) -> GrafanaDashboardConfig:
        return cast(GrafanaDashboardConfig, self._grafana_config)


class BaseGrafanaTool(Tool, ABC):
    """Base class for Grafana tools with common HTTP request functionality."""

    def __init__(self, toolset: GrafanaToolset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    def _make_grafana_request(
        self,
        endpoint: str,
        params: dict,
        query_params: Optional[Dict] = None,
        timeout: int = 30,
    ) -> StructuredToolResult:
        """Make a GET request to Grafana API and return structured result.

        Args:
            endpoint: API endpoint path (e.g., "/api/search")
            params: Original parameters passed to the tool
            query_params: Optional query parameters for the request

        Returns:
            StructuredToolResult with the API response data
        """
        base_url = get_base_url(self._toolset.grafana_config)
        if not base_url.endswith("/"):
            base_url += "/"
        url = urljoin(base_url, endpoint)
        headers = build_headers(
            api_key=self._toolset.grafana_config.api_key,
            additional_headers=self._toolset.grafana_config.headers,
        )

        response = requests.get(
            url,
            headers=headers,
            params=query_params,
            timeout=timeout,
            verify=self._toolset.grafana_config.verify_ssl,
        )
        response.raise_for_status()
        data = response.json()

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            url=url,
            params=params,
        )


class SearchDashboards(BaseGrafanaTool):
    def __init__(self, toolset: GrafanaToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_search_dashboards",
            description="Search for Grafana dashboards and folders by title using the /api/search endpoint. WARNING: This only searches dashboard titles and metadata (tags, folders), NOT the content of dashboard panels or queries. To find dashboards containing a specific metric or query expression, use grafana_search_inside_all_dashboards instead.",
            parameters={
                "query": ToolParameter(
                    description="Search text to filter dashboards",
                    type="string",
                    required=False,
                ),
                "tag": ToolParameter(
                    description="Search dashboards by tag",
                    type="string",
                    required=False,
                ),
                "type": ToolParameter(
                    description="Filter by type: 'dash-folder' or 'dash-db'",
                    type="string",
                    required=False,
                ),
                "dashboardIds": ToolParameter(
                    description="List of dashboard IDs to filter (comma-separated)",
                    type="string",
                    required=False,
                ),
                "dashboardUIDs": ToolParameter(
                    description="List of dashboard UIDs to search for (comma-separated)",
                    type="string",
                    required=False,
                ),
                "folderUIDs": ToolParameter(
                    description="List of folder UIDs to search within (comma-separated)",
                    type="string",
                    required=False,
                ),
                "starred": ToolParameter(
                    description="Return only starred dashboards",
                    type="boolean",
                    required=False,
                ),
                "limit": ToolParameter(
                    description="Maximum results (default 1000, max 5000)",
                    type="integer",
                    required=False,
                ),
                "page": ToolParameter(
                    description="Page number for pagination",
                    type="integer",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        query_params = {}
        if params.get("query"):
            query_params["query"] = params["query"]
        if params.get("tag"):
            query_params["tag"] = params["tag"]
        if params.get("type"):
            query_params["type"] = params["type"]
        if params.get("dashboardIds"):
            # Check if dashboardIds also needs to be passed as multiple params
            dashboard_ids = params["dashboardIds"].split(",")
            query_params["dashboardIds"] = [
                dashboard_id.strip()
                for dashboard_id in dashboard_ids
                if dashboard_id.strip()
            ]
        if params.get("dashboardUIDs"):
            # Handle dashboardUIDs as a list - split comma-separated values
            dashboard_uids = params["dashboardUIDs"].split(",")
            query_params["dashboardUIDs"] = [
                uid.strip() for uid in dashboard_uids if uid.strip()
            ]
        if params.get("folderUIDs"):
            # Check if folderUIDs also needs to be passed as multiple params
            folder_uids = params["folderUIDs"].split(",")
            query_params["folderUIDs"] = [
                uid.strip() for uid in folder_uids if uid.strip()
            ]
        if params.get("starred") is not None:
            query_params["starred"] = str(params["starred"]).lower()
        if params.get("limit"):
            query_params["limit"] = params["limit"]
        if params.get("page"):
            query_params["page"] = params["page"]

        result = self._make_grafana_request("api/search", params, query_params)

        config = self._toolset.grafana_config
        search_url = _build_grafana_dashboard_url(config, query_params=query_params)

        if params.get("dashboardUIDs"):
            uids = [
                uid.strip() for uid in params["dashboardUIDs"].split(",") if uid.strip()
            ]
            if len(uids) == 1:
                search_url = _build_grafana_dashboard_url(config, uid=uids[0])

        return StructuredToolResult(
            status=result.status,
            data=result.data,
            params=result.params,
            url=search_url if search_url else None,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Search Dashboards"


class SearchInsideAllDashboards(BaseGrafanaTool):
    """Search for a text pattern (e.g. a metric name) inside all dashboard definitions.

    This fetches every dashboard's full JSON definition and searches for the
    given text within panel targets, expressions, and templates.  Use this when
    you need to find which dashboards reference a specific metric, label, or
    query expression.
    """

    _SEARCH_LIMIT_PER_PAGE = 1000
    _MAX_DASHBOARDS = 5000

    def __init__(self, toolset: GrafanaToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_search_inside_all_dashboards",
            description=(
                "Search inside all Grafana dashboard definitions for a text pattern "
                "(e.g. a metric name or label). Unlike grafana_search_dashboards which "
                "only searches titles, this tool inspects every dashboard's panel queries, "
                "expressions, and templating variables to find matches."
            ),
            parameters={
                "search_text": ToolParameter(
                    description=(
                        "The text to search for inside dashboard definitions "
                        "(e.g. a Prometheus metric name like 'container_cpu_usage_seconds_total')"
                    ),
                    type="string",
                    required=True,
                ),
            },
        )

    def _fetch_all_dashboard_uids(self, params: dict) -> List[Dict[str, Any]]:
        """Fetch all dashboard summaries using paginated search."""
        all_dashboards: List[Dict[str, Any]] = []
        page = 1
        while len(all_dashboards) < self._MAX_DASHBOARDS:
            query_params = {
                "type": "dash-db",
                "limit": self._SEARCH_LIMIT_PER_PAGE,
                "page": page,
            }
            result = self._make_grafana_request("api/search", params, query_params)
            if not result.data:
                break
            all_dashboards.extend(result.data)
            if len(result.data) < self._SEARCH_LIMIT_PER_PAGE:
                break
            page += 1
        return all_dashboards

    @staticmethod
    def _extract_matching_panels(
        dashboard_json: Dict[str, Any], search_text_lower: str
    ) -> List[str]:
        """Return panel titles whose targets/expressions contain *search_text_lower*."""
        matching_panels: List[str] = []
        panels = dashboard_json.get("panels", [])
        for panel in panels:
            # Panels can be nested inside rows
            inner_panels = panel.get("panels", [])
            for p in [panel] + inner_panels:
                panel_json = json.dumps(p.get("targets", []))
                if search_text_lower in panel_json.lower():
                    matching_panels.append(p.get("title", "<untitled>"))
                    continue
                # Also check datasource expressions stored at panel level
                expr = p.get("expression", "")
                if isinstance(expr, str) and search_text_lower in expr.lower():
                    matching_panels.append(p.get("title", "<untitled>"))
        return matching_panels

    @staticmethod
    def _search_templating(
        dashboard_json: Dict[str, Any], search_text_lower: str
    ) -> bool:
        """Return True if *search_text_lower* appears in any templating variable."""
        templating = dashboard_json.get("templating", {})
        templating_str = json.dumps(templating)
        return search_text_lower in templating_str.lower()

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        search_text = params["search_text"]
        search_text_lower = search_text.lower()

        dashboards = self._fetch_all_dashboard_uids(params)
        total_dashboards = len(dashboards)

        matches: List[Dict[str, Any]] = []

        for dashboard_summary in dashboards:
            uid = dashboard_summary.get("uid")
            if not uid:
                continue

            try:
                detail = self._make_grafana_request(f"api/dashboards/uid/{uid}", params)
            except Exception:
                logger.debug(
                    "Failed to fetch dashboard uid=%s, skipping", uid, exc_info=True
                )
                continue

            dashboard_json = (detail.data or {}).get("dashboard", {})

            matching_panels = self._extract_matching_panels(
                dashboard_json, search_text_lower
            )
            in_templating = self._search_templating(dashboard_json, search_text_lower)

            if matching_panels or in_templating:
                match_info: Dict[str, Any] = {
                    "uid": uid,
                    "title": dashboard_summary.get("title"),
                    "folderTitle": dashboard_summary.get("folderTitle"),
                    "url": dashboard_summary.get("url"),
                }
                if matching_panels:
                    match_info["matching_panels"] = matching_panels
                if in_templating:
                    match_info["found_in_templating_variables"] = True
                matches.append(match_info)

        config = self._toolset.grafana_config
        search_url = _build_grafana_dashboard_url(config)

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={
                "search_text": search_text,
                "total_dashboards_searched": total_dashboards,
                "matching_dashboards": matches,
            },
            params=params,
            url=search_url,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Search Inside All Dashboards for '{params.get('search_text', '')}'"


class GetDashboardByUID(JsonFilterMixin, BaseGrafanaTool):
    def __init__(self, toolset: GrafanaToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_get_dashboard_by_uid",
            description="Get a dashboard by its UID using the /api/dashboards/uid/:uid endpoint",
            parameters=self.extend_parameters(
                {
                    "uid": ToolParameter(
                        description="The unique identifier of the dashboard",
                        type="string",
                        required=True,
                    )
                }
            ),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        uid = params["uid"]
        result = self._make_grafana_request(f"api/dashboards/uid/{uid}", params)

        dashboard_url = _build_grafana_dashboard_url(
            self._toolset.grafana_config, uid=uid
        )

        filtered_result = self.filter_result(result, params)
        filtered_result.url = dashboard_url if dashboard_url else result.url
        return filtered_result

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Dashboard {params.get('uid', '')}"


class GetHomeDashboard(JsonFilterMixin, BaseGrafanaTool):
    def __init__(self, toolset: GrafanaToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_get_home_dashboard",
            description="Get the home dashboard using the /api/dashboards/home endpoint",
            parameters=self.extend_parameters({}),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        result = self._make_grafana_request("api/dashboards/home", params)
        config = self._toolset.grafana_config
        dashboard_url = None
        if isinstance(result.data, dict):
            uid = result.data.get("dashboard", {}).get("uid")
            if uid:
                dashboard_url = _build_grafana_dashboard_url(config, uid=uid)

        filtered_result = self.filter_result(result, params)
        filtered_result.url = dashboard_url if dashboard_url else None
        return filtered_result

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Home Dashboard"


class GetDashboardTags(BaseGrafanaTool):
    def __init__(self, toolset: GrafanaToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_get_dashboard_tags",
            description="Get all tags used across dashboards using the /api/dashboards/tags endpoint",
            parameters={},
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        result = self._make_grafana_request("api/dashboards/tags", params)

        config = self._toolset.grafana_config
        tags_url = _build_grafana_dashboard_url(config)

        return StructuredToolResult(
            status=result.status,
            data=result.data,
            params=result.params,
            url=tags_url,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Dashboard Tags"
