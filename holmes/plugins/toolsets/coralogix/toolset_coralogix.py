import json
import os
from typing import Any, Optional, Tuple

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolsetTag,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
)
from holmes.plugins.toolsets.consts import TOOLSET_CONFIG_MISSING_ERROR
from holmes.plugins.toolsets.coralogix.api import (
    health_check,
    execute_dataprime_query,
    CoralogixTier,
    _drop_raw_result_lines,
)
from holmes.plugins.toolsets.coralogix.utils import CoralogixConfig
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.keygen_utils import generate_random_key


class ExecuteDataPrimeQuery(Tool):
    def __init__(self, toolset: "CoralogixToolset"):
        super().__init__(
            name="coralogix_execute_dataprime_query",
            description="Execute a DataPrime query against Coralogix to fetch logs, traces, metrics, and other telemetry data. "
            "Returns the raw query results from Coralogix.",
            parameters={
                "query": ToolParameter(
                    description="DataPrime query string. Examples: `source logs | lucene 'error' | limit 100`, `source traces | filter serviceName == 'my-service' | limit 100`. Always include a `limit` clause.",
                    type="string",
                    required=True,
                ),
                "description": ToolParameter(
                    description="Brief 6-word description of the query.",
                    type="string",
                    required=True,
                ),
                "query_type": ToolParameter(
                    description="'Logs', 'Traces', 'Metrics', 'Discover Data' or 'Other'.",
                    type="string",
                    required=True,
                ),
                "start_date": ToolParameter(
                    description="Optional start date in RFC3339 format (e.g., '2024-01-01T00:00:00Z').",
                    type="string",
                    required=True,
                ),
                "end_date": ToolParameter(
                    description="Optional end date in RFC3339 format (e.g., '2024-01-01T23:59:59Z').",
                    type="string",
                    required=True,
                ),
                "tier": ToolParameter(
                    description="Optional tier: 'FREQUENT_SEARCH' or 'ARCHIVE'.",
                    type="string",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self._toolset.coralogix_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Coralogix toolset is not configured",
                params=params,
            )

        tier = None
        if tier_str := params.get("tier"):
            try:
                tier = CoralogixTier[tier_str]
            except KeyError:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Invalid tier '{tier_str}'. Must be 'FREQUENT_SEARCH' or 'ARCHIVE'",
                    params=params,
                )

        result_text, error = execute_dataprime_query(
            domain=self._toolset.coralogix_config.domain,
            api_key=self._toolset.coralogix_config.api_key,
            dataprime_query=params["query"],
            start_date=params.get("start_date"),
            end_date=params.get("end_date"),
            tier=tier,
        )

        if error:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error,
                params=params,
            )

        # Parse the JSON string so the final output can embed structured data
        # instead of an escaped blob. This keeps the response readable and
        # avoids double-escaping that wastes tokens.
        parsed_data: Any
        try:
            parsed_data = json.loads(result_text) if result_text else None
            parsed_data = _drop_raw_result_lines(parsed_data)
        except json.JSONDecodeError:
            # Fall back to the raw text if JSON decoding fails
            parsed_data = result_text

        result_with_key = {
            "random_key": generate_random_key(),
            "tool_name": self.name,
            "query": params["query"],
            "data": parsed_data,
            "domain": self._toolset.coralogix_config.domain,
            "team_hostname": self._toolset.coralogix_config.team_hostname,
        }

        # Return a pretty-printed JSON string for readability by the model/user.
        final_result = json.dumps(result_with_key, indent=2, sort_keys=False)
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=final_result,
            params=params,
        )

    def get_parameterized_one_liner(self, params) -> str:
        description = params.get("description", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Execute DataPrime ({description})"


class CoralogixToolset(Toolset):
    def __init__(self):
        super().__init__(
            name="coralogix",
            description="Toolset for interacting with Coralogix to fetch logs, traces, metrics, and execute DataPrime queries",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/coralogix-logs/",
            icon_url="https://avatars.githubusercontent.com/u/35295744?s=200&v=4",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[ExecuteDataPrimeQuery(self)],
            tags=[ToolsetTag.CORE],
        )
        template_path = os.path.join(os.path.dirname(__file__), "coralogix.jinja2")
        if os.path.exists(template_path):
            self._load_llm_instructions(
                jinja_template=f"file://{os.path.abspath(template_path)}"
            )

    def get_example_config(self):
        example_config = CoralogixConfig(
            api_key="<cxuw_...>", team_hostname="my-team", domain="eu2.coralogix.com"
        )
        return example_config.model_dump()

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return False, TOOLSET_CONFIG_MISSING_ERROR

        self.config = CoralogixConfig(**config)

        if not self.config.api_key:
            return False, "Missing configuration field 'api_key'"

        return health_check(domain=self.config.domain, api_key=self.config.api_key)

    @property
    def coralogix_config(self) -> Optional[CoralogixConfig]:
        return self.config
