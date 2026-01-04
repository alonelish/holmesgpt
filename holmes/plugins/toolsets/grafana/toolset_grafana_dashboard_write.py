import json
import logging
import os
from abc import ABC
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type, cast
from urllib.parse import urljoin

import requests

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
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

logger = logging.getLogger(__name__)


class GrafanaDashboardWriteConfig(GrafanaConfig):
    """Configuration for Grafana Dashboard Write toolset."""

    healthcheck: Optional[str] = "api/health"


class GrafanaDashboardWriteToolset(BaseGrafanaToolset):
    """Toolset for writing/updating Grafana dashboards with user approval."""

    config_class: ClassVar[Type[GrafanaDashboardWriteConfig]] = (
        GrafanaDashboardWriteConfig
    )

    def __init__(self):
        super().__init__(
            name="grafana/dashboards-write",
            description=(
                "Provides tools for creating and updating Grafana dashboards. "
                "All write operations require user approval before execution."
            ),
            icon_url="https://w7.pngwing.com/pngs/434/923/png-transparent-grafana-hd-logo-thumbnail.png",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/grafanadashboards/",
            tools=[
                CreateOrUpdateDashboard(self),
                MergeDashboardUpdate(self),
                PatchDashboardPanel(self),
                DeleteDashboard(self),
            ],
        )

        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "toolset_grafana_dashboard_write.jinja2"
        )

    def health_check(self) -> Tuple[bool, str]:
        """Test connectivity by checking Grafana health endpoint."""
        try:
            url = urljoin(
                get_base_url(self.grafana_config),
                self.grafana_config.healthcheck or "api/health",
            )
            headers = build_headers(
                api_key=self.grafana_config.api_key,
                additional_headers=self.grafana_config.headers,
            )
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            return True, ""
        except Exception as e:
            return False, f"Failed to connect to Grafana: {str(e)}"

    @property
    def grafana_config(self) -> GrafanaDashboardWriteConfig:
        return cast(GrafanaDashboardWriteConfig, self._grafana_config)


class BaseGrafanaWriteTool(Tool, ABC):
    """Base class for Grafana write tools with common HTTP request functionality."""

    def __init__(self, toolset: GrafanaDashboardWriteToolset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    def _make_grafana_get_request(
        self,
        endpoint: str,
        params: dict,
        timeout: int = 30,
    ) -> StructuredToolResult:
        """Make a GET request to Grafana API."""
        url = urljoin(get_base_url(self._toolset.grafana_config), endpoint)
        headers = build_headers(
            api_key=self._toolset.grafana_config.api_key,
            additional_headers=self._toolset.grafana_config.headers,
        )

        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            url=url,
            params=params,
        )

    def _make_grafana_post_request(
        self,
        endpoint: str,
        params: dict,
        json_body: dict,
        timeout: int = 30,
    ) -> StructuredToolResult:
        """Make a POST request to Grafana API."""
        url = urljoin(get_base_url(self._toolset.grafana_config), endpoint)
        headers = build_headers(
            api_key=self._toolset.grafana_config.api_key,
            additional_headers=self._toolset.grafana_config.headers,
        )

        response = requests.post(url, headers=headers, json=json_body, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            url=url,
            params=params,
        )

    def _make_grafana_delete_request(
        self,
        endpoint: str,
        params: dict,
        timeout: int = 30,
    ) -> StructuredToolResult:
        """Make a DELETE request to Grafana API."""
        url = urljoin(get_base_url(self._toolset.grafana_config), endpoint)
        headers = build_headers(
            api_key=self._toolset.grafana_config.api_key,
            additional_headers=self._toolset.grafana_config.headers,
        )

        response = requests.delete(url, headers=headers, timeout=timeout)
        response.raise_for_status()

        # DELETE may return empty response
        try:
            data = response.json()
        except Exception:
            data = {"status": "success", "message": "Dashboard deleted"}

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            url=url,
            params=params,
        )

    def _get_dashboard_by_uid(self, uid: str) -> Dict[str, Any]:
        """Fetch a dashboard by UID and return the full response."""
        result = self._make_grafana_get_request(
            f"/api/dashboards/uid/{uid}", {"uid": uid}
        )
        if result.status != StructuredToolResultStatus.SUCCESS:
            raise ValueError(f"Failed to fetch dashboard: {result.error}")
        if not isinstance(result.data, dict):
            raise ValueError(f"Unexpected response type: {type(result.data)}")
        return result.data

    def _build_approval_description(self, action: str, details: str) -> str:
        """Build a human-readable description for approval prompt."""
        return f"{action}: {details}"


class CreateOrUpdateDashboard(BaseGrafanaWriteTool):
    """Create a new dashboard or fully replace an existing one."""

    def __init__(self, toolset: GrafanaDashboardWriteToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_create_or_update_dashboard",
            description=(
                "Create a new Grafana dashboard or fully replace an existing one. "
                "Requires user approval. Use this for creating new dashboards or "
                "when you have the complete dashboard JSON to replace."
            ),
            parameters={
                "dashboard_json": ToolParameter(
                    description=(
                        "The complete dashboard JSON object. Must include 'title'. "
                        "Set 'uid' to update existing dashboard, omit for new."
                    ),
                    type="string",
                    required=True,
                ),
                "folder_uid": ToolParameter(
                    description="The UID of the folder to save the dashboard in",
                    type="string",
                    required=False,
                ),
                "message": ToolParameter(
                    description="Commit message describing the changes",
                    type="string",
                    required=False,
                ),
                "overwrite": ToolParameter(
                    description="Set to true to overwrite existing dashboard with same UID",
                    type="boolean",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        dashboard_json_str = params.get("dashboard_json", "{}")

        try:
            dashboard = json.loads(dashboard_json_str)
        except json.JSONDecodeError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Invalid JSON in dashboard_json: {str(e)}",
                params=params,
            )

        title = dashboard.get("title", "Untitled Dashboard")
        uid = dashboard.get("uid", "new")

        # Require approval for all write operations
        if not context.user_approved:
            action = "UPDATE" if uid != "new" else "CREATE"
            return StructuredToolResult(
                status=StructuredToolResultStatus.APPROVAL_REQUIRED,
                error="Dashboard write operation requires user approval",
                params=params,
                invocation=self._build_approval_description(
                    f"{action} dashboard",
                    f"title='{title}', uid='{uid}'",
                ),
            )

        # Build the request body
        request_body: Dict[str, Any] = {
            "dashboard": dashboard,
            "overwrite": params.get("overwrite", False),
        }

        if params.get("folder_uid"):
            request_body["folderUid"] = params["folder_uid"]
        if params.get("message"):
            request_body["message"] = params["message"]

        try:
            return self._make_grafana_post_request(
                "/api/dashboards/db", params, request_body
            )
        except requests.HTTPError as e:
            error_detail = ""
            if e.response is not None:
                try:
                    error_detail = e.response.json().get("message", str(e))
                except Exception:
                    error_detail = e.response.text[:500]
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to save dashboard: {error_detail or str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        try:
            dashboard = json.loads(params.get("dashboard_json", "{}"))
            title = dashboard.get("title", "Unknown")
        except Exception:
            title = "Unknown"
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Create/Update Dashboard '{title}'"


class MergeDashboardUpdate(BaseGrafanaWriteTool):
    """Merge updates into an existing dashboard without replacing it entirely."""

    def __init__(self, toolset: GrafanaDashboardWriteToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_merge_dashboard_update",
            description=(
                "Update specific fields of an existing dashboard without replacing "
                "the entire dashboard. Fetches the current dashboard, merges your "
                "changes, and saves. Good for updating title, tags, variables, etc."
            ),
            parameters={
                "uid": ToolParameter(
                    description="The UID of the dashboard to update",
                    type="string",
                    required=True,
                ),
                "updates_json": ToolParameter(
                    description=(
                        "JSON object with fields to update. Will be merged with "
                        "existing dashboard. Example: {\"title\": \"New Title\", "
                        "\"tags\": [\"production\", \"metrics\"]}"
                    ),
                    type="string",
                    required=True,
                ),
                "message": ToolParameter(
                    description="Commit message describing the changes",
                    type="string",
                    required=False,
                ),
            },
        )

    def _deep_merge(self, base: dict, updates: dict) -> dict:
        """Deep merge updates into base dictionary."""
        result = base.copy()
        for key, value in updates.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        uid = params.get("uid", "")
        updates_json_str = params.get("updates_json", "{}")

        if not uid:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Dashboard UID is required",
                params=params,
            )

        try:
            updates = json.loads(updates_json_str)
        except json.JSONDecodeError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Invalid JSON in updates_json: {str(e)}",
                params=params,
            )

        # Require approval for all write operations
        if not context.user_approved:
            update_keys = list(updates.keys())
            return StructuredToolResult(
                status=StructuredToolResultStatus.APPROVAL_REQUIRED,
                error="Dashboard merge update requires user approval",
                params=params,
                invocation=self._build_approval_description(
                    "MERGE UPDATE dashboard",
                    f"uid='{uid}', updating fields: {update_keys}",
                ),
            )

        # Fetch existing dashboard
        try:
            existing = self._get_dashboard_by_uid(uid)
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to fetch existing dashboard: {str(e)}",
                params=params,
            )

        # Extract dashboard and meta
        dashboard = existing.get("dashboard", {})
        meta = existing.get("meta", {})

        # Merge updates
        merged_dashboard = self._deep_merge(dashboard, updates)

        # Preserve uid and ensure version is set for update
        merged_dashboard["uid"] = uid
        merged_dashboard["id"] = dashboard.get("id")
        merged_dashboard["version"] = dashboard.get("version", 1)

        # Build request
        request_body: Dict[str, Any] = {
            "dashboard": merged_dashboard,
            "overwrite": True,
        }

        if meta.get("folderUid"):
            request_body["folderUid"] = meta["folderUid"]
        if params.get("message"):
            request_body["message"] = params["message"]

        try:
            return self._make_grafana_post_request(
                "/api/dashboards/db", params, request_body
            )
        except requests.HTTPError as e:
            error_detail = ""
            if e.response is not None:
                try:
                    error_detail = e.response.json().get("message", str(e))
                except Exception:
                    error_detail = e.response.text[:500]
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to update dashboard: {error_detail or str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        uid = params.get("uid", "Unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Merge Update Dashboard '{uid}'"


class PatchDashboardPanel(BaseGrafanaWriteTool):
    """Patch a specific panel in a dashboard without loading the entire dashboard JSON.

    This is especially useful for huge dashboards where the entire JSON doesn't fit
    in the LLM context window. The LLM can list panel IDs/titles first, then patch
    a specific panel.
    """

    def __init__(self, toolset: GrafanaDashboardWriteToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_patch_dashboard_panel",
            description=(
                "Update a specific panel in a dashboard by panel ID or title. "
                "Ideal for huge dashboards where the full JSON doesn't fit in context. "
                "First use grafana_get_dashboard_by_uid to list panel IDs/titles, "
                "then patch the specific panel you need to modify."
            ),
            parameters={
                "dashboard_uid": ToolParameter(
                    description="The UID of the dashboard containing the panel",
                    type="string",
                    required=True,
                ),
                "panel_id": ToolParameter(
                    description="The numeric ID of the panel to update (preferred)",
                    type="integer",
                    required=False,
                ),
                "panel_title": ToolParameter(
                    description="The title of the panel to update (if panel_id not known)",
                    type="string",
                    required=False,
                ),
                "panel_updates_json": ToolParameter(
                    description=(
                        "JSON object with panel fields to update. Will be merged "
                        "with existing panel. Example: {\"title\": \"New Title\", "
                        "\"targets\": [{...}]}"
                    ),
                    type="string",
                    required=True,
                ),
                "message": ToolParameter(
                    description="Commit message describing the changes",
                    type="string",
                    required=False,
                ),
            },
        )

    def _find_panel(
        self, panels: List[dict], panel_id: Optional[int], panel_title: Optional[str]
    ) -> Tuple[Optional[dict], Optional[int]]:
        """Find a panel by ID or title, returns (panel, index) or (None, None)."""
        for idx, panel in enumerate(panels):
            if panel_id is not None and panel.get("id") == panel_id:
                return panel, idx
            if panel_title and panel.get("title") == panel_title:
                return panel, idx

            # Check nested panels in rows
            if panel.get("type") == "row" and "panels" in panel:
                nested_result = self._find_panel(
                    panel["panels"], panel_id, panel_title
                )
                if nested_result[0] is not None:
                    return nested_result

        return None, None

    def _deep_merge(self, base: dict, updates: dict) -> dict:
        """Deep merge updates into base dictionary."""
        result = base.copy()
        for key, value in updates.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def _update_panel_in_list(
        self,
        panels: List[dict],
        panel_id: Optional[int],
        panel_title: Optional[str],
        updates: dict,
    ) -> bool:
        """Update a panel in place within the panels list. Returns True if found."""
        for idx, panel in enumerate(panels):
            if panel_id is not None and panel.get("id") == panel_id:
                panels[idx] = self._deep_merge(panel, updates)
                return True
            if panel_title and panel.get("title") == panel_title:
                panels[idx] = self._deep_merge(panel, updates)
                return True

            # Check nested panels in rows
            if panel.get("type") == "row" and "panels" in panel:
                if self._update_panel_in_list(
                    panel["panels"], panel_id, panel_title, updates
                ):
                    return True

        return False

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        dashboard_uid = params.get("dashboard_uid", "")
        panel_id = params.get("panel_id")
        panel_title = params.get("panel_title")
        panel_updates_str = params.get("panel_updates_json", "{}")

        if not dashboard_uid:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Dashboard UID is required",
                params=params,
            )

        if panel_id is None and not panel_title:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Either panel_id or panel_title is required",
                params=params,
            )

        try:
            panel_updates = json.loads(panel_updates_str)
        except json.JSONDecodeError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Invalid JSON in panel_updates_json: {str(e)}",
                params=params,
            )

        # Require approval for all write operations
        if not context.user_approved:
            panel_identifier = f"id={panel_id}" if panel_id else f"title='{panel_title}'"
            update_keys = list(panel_updates.keys())
            return StructuredToolResult(
                status=StructuredToolResultStatus.APPROVAL_REQUIRED,
                error="Panel patch requires user approval",
                params=params,
                invocation=self._build_approval_description(
                    "PATCH panel",
                    f"dashboard='{dashboard_uid}', panel {panel_identifier}, "
                    f"updating fields: {update_keys}",
                ),
            )

        # Fetch existing dashboard
        try:
            existing = self._get_dashboard_by_uid(dashboard_uid)
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to fetch dashboard: {str(e)}",
                params=params,
            )

        dashboard = existing.get("dashboard", {})
        meta = existing.get("meta", {})
        panels = dashboard.get("panels", [])

        # Find and verify panel exists
        panel, _ = self._find_panel(panels, panel_id, panel_title)
        if panel is None:
            panel_identifier = f"id={panel_id}" if panel_id else f"title='{panel_title}'"
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Panel not found: {panel_identifier}",
                params=params,
            )

        # Update panel in place
        if not self._update_panel_in_list(panels, panel_id, panel_title, panel_updates):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Failed to update panel in dashboard",
                params=params,
            )

        # Build request
        request_body: Dict[str, Any] = {
            "dashboard": dashboard,
            "overwrite": True,
        }

        if meta.get("folderUid"):
            request_body["folderUid"] = meta["folderUid"]
        if params.get("message"):
            request_body["message"] = params["message"]

        try:
            result = self._make_grafana_post_request(
                "/api/dashboards/db", params, request_body
            )
            # Add panel info to the result
            if result.status == StructuredToolResultStatus.SUCCESS:
                result.data = {
                    **(result.data if isinstance(result.data, dict) else {}),
                    "patched_panel": {
                        "id": panel.get("id"),
                        "title": panel.get("title"),
                        "updated_fields": list(panel_updates.keys()),
                    },
                }
            return result
        except requests.HTTPError as e:
            error_detail = ""
            if e.response is not None:
                try:
                    error_detail = e.response.json().get("message", str(e))
                except Exception:
                    error_detail = e.response.text[:500]
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to save dashboard: {error_detail or str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        dashboard_uid = params.get("dashboard_uid", "Unknown")
        panel_id = params.get("panel_id")
        panel_title = params.get("panel_title")
        panel_str = f"id={panel_id}" if panel_id else f"'{panel_title}'"
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Patch Panel {panel_str} in '{dashboard_uid}'"


class DeleteDashboard(BaseGrafanaWriteTool):
    """Delete a dashboard by UID."""

    def __init__(self, toolset: GrafanaDashboardWriteToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_delete_dashboard",
            description=(
                "Delete a Grafana dashboard by its UID. "
                "This is a destructive operation and requires user approval."
            ),
            parameters={
                "uid": ToolParameter(
                    description="The UID of the dashboard to delete",
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        uid = params.get("uid", "")

        if not uid:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Dashboard UID is required",
                params=params,
            )

        # Require approval for delete operations
        if not context.user_approved:
            return StructuredToolResult(
                status=StructuredToolResultStatus.APPROVAL_REQUIRED,
                error="Dashboard deletion requires user approval",
                params=params,
                invocation=self._build_approval_description(
                    "DELETE dashboard",
                    f"uid='{uid}'",
                ),
            )

        try:
            return self._make_grafana_delete_request(
                f"/api/dashboards/uid/{uid}", params
            )
        except requests.HTTPError as e:
            error_detail = ""
            if e.response is not None:
                try:
                    error_detail = e.response.json().get("message", str(e))
                except Exception:
                    error_detail = e.response.text[:500]
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to delete dashboard: {error_detail or str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        uid = params.get("uid", "Unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Delete Dashboard '{uid}'"
