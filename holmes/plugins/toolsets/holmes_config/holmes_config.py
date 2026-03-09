import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import yaml  # type: ignore
from pydantic import PrivateAttr

from holmes.common.env_vars import ROBUSTA_CONFIG_PATH
from holmes.core.config import config_path_dir
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.definitions import CUSTOM_TOOLSET_LOCATION

if TYPE_CHECKING:
    from holmes.core.tools_utils.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)


class ListToolsets(Tool):
    toolset: "HolmesConfigToolset"

    def __init__(self, toolset: "HolmesConfigToolset"):
        super().__init__(
            name="holmes_list_toolsets",
            description=(
                "List all Holmes toolsets and their status (enabled, disabled, failed). "
                "Use this to understand which data sources and integrations are currently "
                "available or to diagnose why a toolset is not working."
            ),
            parameters={
                "status_filter": ToolParameter(
                    description=(
                        "Optional filter: 'enabled', 'disabled', or 'failed'. "
                        "If omitted, returns all toolsets."
                    ),
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,  # type: ignore
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        tool_executor = self.toolset.tool_executor
        if tool_executor is None:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Tool executor not available for config inspection",
            )

        status_filter = params.get("status_filter")

        toolsets_info = []
        for ts in tool_executor.toolsets:
            info: Dict[str, Any] = {
                "name": ts.name,
                "enabled": ts.enabled,
                "status": ts.status.value,
                "description": ts.description,
                "type": ts.type.value if ts.type else None,
                "num_tools": len(ts.tools),
            }
            if ts.error:
                info["error"] = ts.error
            if ts.docs_url:
                info["docs_url"] = ts.docs_url

            toolsets_info.append(info)

        if status_filter:
            status_filter = status_filter.lower().strip()
            if status_filter == "enabled":
                toolsets_info = [t for t in toolsets_info if t["status"] == "enabled"]
            elif status_filter == "disabled":
                toolsets_info = [t for t in toolsets_info if t["status"] == "disabled"]
            elif status_filter == "failed":
                toolsets_info = [t for t in toolsets_info if t["status"] == "failed"]

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=toolsets_info,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        status_filter = params.get("status_filter", "all")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List toolsets (filter={status_filter})"


class GetToolsetDetails(Tool):
    toolset: "HolmesConfigToolset"

    def __init__(self, toolset: "HolmesConfigToolset"):
        super().__init__(
            name="holmes_get_toolset_details",
            description=(
                "Get detailed information about a specific Holmes toolset, including "
                "its configuration, tools it provides, prerequisites, and error details. "
                "Use this to diagnose why a specific toolset is failing or misconfigured."
            ),
            parameters={
                "toolset_name": ToolParameter(
                    description="The name of the toolset to inspect (e.g. 'kubernetes', 'prometheus', 'grafana/dashboards')",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,  # type: ignore
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        tool_executor = self.toolset.tool_executor
        if tool_executor is None:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Tool executor not available for config inspection",
            )

        toolset_name = params["toolset_name"]

        target_toolset = None
        for ts in tool_executor.toolsets:
            if ts.name == toolset_name:
                target_toolset = ts
                break

        if target_toolset is None:
            available = [ts.name for ts in tool_executor.toolsets]
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Toolset '{toolset_name}' not found. Available toolsets: {', '.join(sorted(available))}",
            )

        tools_info = []
        for tool in target_toolset.tools:
            tool_info: Dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
            }
            if tool.parameters:
                tool_info["parameters"] = {
                    name: {
                        "type": p.type,
                        "description": p.description,
                        "required": p.required,
                    }
                    for name, p in tool.parameters.items()
                }
            tools_info.append(tool_info)

        prerequisites_info = []
        for prereq in target_toolset.prerequisites:
            prereq_data = prereq.model_dump(exclude_none=True)
            # Remove callable references that aren't serializable
            prereq_data.pop("callable", None)
            if prereq_data:
                prerequisites_info.append(prereq_data)

        result: Dict[str, Any] = {
            "name": target_toolset.name,
            "description": target_toolset.description,
            "enabled": target_toolset.enabled,
            "status": target_toolset.status.value,
            "type": target_toolset.type.value if target_toolset.type else None,
            "tools": tools_info,
        }

        if target_toolset.error:
            result["error"] = target_toolset.error
        if target_toolset.docs_url:
            result["docs_url"] = target_toolset.docs_url
        if prerequisites_info:
            result["prerequisites"] = prerequisites_info
        if target_toolset.llm_instructions:
            result["llm_instructions"] = target_toolset.llm_instructions

        # Include config example and schema for toolsets that have config classes
        config_example = target_toolset.get_config_example()
        if config_example:
            result["config_example"] = config_example

        config_schema = target_toolset.get_config_schema()
        if config_schema:
            result["config_schema"] = config_schema

        # Include the actual config values (if any), masking sensitive values
        if target_toolset.config:
            result["current_config"] = _mask_sensitive_config(target_toolset.config)

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=result,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        name = params.get("toolset_name", "<unknown>")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get details for toolset '{name}'"


def _mask_sensitive_config(config: Any) -> Any:
    """Mask values that look like secrets/keys/passwords in config dicts."""
    sensitive_patterns = {"key", "token", "password", "secret", "credential", "auth"}

    if isinstance(config, dict):
        masked = {}
        for k, v in config.items():
            if any(pattern in k.lower() for pattern in sensitive_patterns) and v:
                masked[k] = "***REDACTED***"
            else:
                masked[k] = _mask_sensitive_config(v)
        return masked
    elif isinstance(config, list):
        return [_mask_sensitive_config(item) for item in config]
    return config


DEFAULT_CONFIG_LOCATION = os.path.join(config_path_dir, "config.yaml")

# Known config file locations to search (in priority order)
_CONFIG_FILE_CANDIDATES: List[str] = [
    DEFAULT_CONFIG_LOCATION,
    CUSTOM_TOOLSET_LOCATION,
    ROBUSTA_CONFIG_PATH,
]


def _find_config_files() -> List[str]:
    """Return list of existing config file paths."""
    found: List[str] = []
    for path in _CONFIG_FILE_CANDIDATES:
        if path and os.path.isfile(path):
            found.append(path)
    return found


class GetHolmesConfigFile(Tool):
    toolset: "HolmesConfigToolset"

    def __init__(self, toolset: "HolmesConfigToolset"):
        super().__init__(
            name="holmes_get_config_file",
            description=(
                "Read the Holmes configuration file content. This shows the raw YAML "
                "configuration including toolset settings, API URLs, and custom toolset "
                "definitions. Use this to inspect how Holmes is configured and to diagnose "
                "misconfigured URLs, wrong settings, or missing configuration. "
                "Sensitive values (API keys, tokens, passwords) are redacted."
            ),
            parameters={
                "file_path": ToolParameter(
                    description=(
                        "Optional: specific config file path to read. "
                        "If omitted, reads the main Holmes config file."
                    ),
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,  # type: ignore
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        requested_path = params.get("file_path")

        if requested_path:
            if not os.path.isfile(requested_path):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Config file not found at '{requested_path}'",
                )
            files_to_read = [requested_path]
        else:
            files_to_read = _find_config_files()

        if not files_to_read:
            return StructuredToolResult(
                status=StructuredToolResultStatus.NO_DATA,
                error=(
                    f"No Holmes config file found. "
                    f"Searched locations: {', '.join(_CONFIG_FILE_CANDIDATES)}"
                ),
            )

        results: Dict[str, Any] = {}
        for file_path in files_to_read:
            try:
                with open(file_path) as f:
                    content = yaml.safe_load(f)
                if content:
                    content = _mask_sensitive_config(content)
                results[file_path] = content
            except Exception as e:
                results[file_path] = {"error": f"Failed to read: {e}"}

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=results,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        file_path = params.get("file_path", "default")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Read config file ({file_path})"


class HolmesConfigToolset(Toolset):
    _tool_executor: Optional["ToolExecutor"] = PrivateAttr(default=None)

    def __init__(self):
        super().__init__(
            name="holmes/config",
            description="Inspect Holmes's own configuration: list toolsets, check their status, and diagnose misconfigurations",
            icon_url="https://platform.robusta.dev/demos/holmesgpt.svg",
            prerequisites=[],
            tools=[
                ListToolsets(self),
                GetToolsetDetails(self),
                GetHolmesConfigFile(self),
            ],
            tags=[ToolsetTag.CORE],
            is_default=True,
        )

    @property
    def tool_executor(self) -> Optional["ToolExecutor"]:
        return self._tool_executor

    def set_tool_executor(self, tool_executor: "ToolExecutor") -> None:
        self._tool_executor = tool_executor
