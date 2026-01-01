import json
import logging
import os
from typing import Any, Dict, Optional

import requests  # type: ignore
from requests import RequestException

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.json_filter_mixin import JsonFilterMixin
from holmes.plugins.toolsets.opensearch.opensearch_utils import (
    BaseOpenSearchConfig,
    BaseOpenSearchToolset,
    add_auth_header,
)
from holmes.plugins.toolsets.utils import get_param_or_raise, toolset_name_for_one_liner


class OpenSearchSearch(JsonFilterMixin, Tool):
    """Generic OpenSearch _search API with optimization support"""

    def __init__(self, toolset: "OpenSearchSearchToolset"):
        super().__init__(
            name="opensearch_search",
            description=(
                "Execute an OpenSearch _search query. Supports document retrieval, filtering, "
                "pagination, and various optimizations. Use this for querying logs, traces, metrics, "
                "or any indexed data."
            ),
            parameters=self.extend_parameters({
                "index": ToolParameter(
                    description="Index pattern to search (e.g., 'logs-*', 'traces-*', 'my-index')",
                    type="string",
                    required=True,
                ),
                "query": ToolParameter(
                    description=(
                        "OpenSearch query DSL as stringified JSON. Example: "
                        '{"bool": {"filter": [{"range": {"@timestamp": {"gte": "now-1h"}}}]}}. '
                        "Use filter context for cacheable queries."
                    ),
                    type="string",
                    required=True,
                ),
                "size": ToolParameter(
                    description="Number of documents to return (default: 100, max: 10000)",
                    type="integer",
                    required=False,
                ),
                "source_fields": ToolParameter(
                    description=(
                        "Comma-separated list of fields to return in _source. "
                        "Example: '@timestamp,message,service.name'. Dramatically reduces payload size."
                    ),
                    type="string",
                    required=False,
                ),
                "track_total_hits": ToolParameter(
                    description=(
                        "Whether to track total hit count. Set to false for faster queries when "
                        "exact count isn't needed. Default: false."
                    ),
                    type="boolean",
                    required=False,
                ),
                "timeout": ToolParameter(
                    description="Query timeout (e.g., '30s', '1m'). Default: '30s'",
                    type="string",
                    required=False,
                ),
                "sort": ToolParameter(
                    description=(
                        "Sort specification as stringified JSON. Example: "
                        '[{"@timestamp": {"order": "desc"}}]'
                    ),
                    type="string",
                    required=False,
                ),
                "search_after": ToolParameter(
                    description=(
                        "Values from the previous page's last hit for pagination. "
                        "Use with sort parameter. Example: '[1609459200000, \"doc-id-123\"]'"
                    ),
                    type="string",
                    required=False,
                ),
                "pit_id": ToolParameter(
                    description=(
                        "Point-in-Time ID for consistent pagination. "
                        "Use opensearch_pit_open to create a PIT first."
                    ),
                    type="string",
                    required=False,
                ),
            }),
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        err_msg = ""
        try:
            # Build the search request body
            query_str = get_param_or_raise(params, "query")
            query_obj = json.loads(query_str)

            body: Dict[str, Any] = {
                "query": query_obj,
                "size": params.get("size", 100),
                "track_total_hits": params.get("track_total_hits", False),
                "timeout": params.get("timeout", "30s"),
            }

            # Add _source filtering if specified
            if params.get("source_fields"):
                source_fields = [f.strip() for f in params["source_fields"].split(",")]
                body["_source"] = source_fields

            # Add sort if specified
            if params.get("sort"):
                body["sort"] = json.loads(params["sort"])

            # Add search_after for pagination
            if params.get("search_after"):
                body["search_after"] = json.loads(params["search_after"])

            # Add PIT if specified
            if params.get("pit_id"):
                body["pit"] = {
                    "id": params["pit_id"],
                    "keep_alive": "5m"
                }

            # Determine the URL
            config = self._toolset.opensearch_config
            if params.get("pit_id"):
                # When using PIT, don't include index in URL
                url = f"{config.opensearch_url}/_search"
            else:
                index = get_param_or_raise(params, "index")
                url = f"{config.opensearch_url}/{index}/_search"

            headers = {"Content-Type": "application/json"}
            headers.update(add_auth_header(config.opensearch_auth_header))

            logging.debug(f"OpenSearch search query: {json.dumps(body)}")

            response = requests.post(
                url=url,
                timeout=180,
                verify=True,
                data=json.dumps(body),
                headers=headers,
            )

            if response.status_code > 300:
                err_msg = response.text

            response.raise_for_status()
            result = StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response.json(),
                params=params,
            )
            return self.filter_result(result, params)

        except requests.Timeout:
            logging.warning("Timeout while executing OpenSearch search", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Request timed out while executing OpenSearch search {err_msg}",
                params=params,
            )
        except RequestException as e:
            logging.warning("Failed to execute OpenSearch search", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Network error while executing OpenSearch search {err_msg}: {str(e)}",
                params=params,
            )
        except Exception as e:
            logging.warning("Failed to process OpenSearch search", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error {err_msg}: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        index = params.get("index", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Search ({index})"


class OpenSearchMultiSearch(JsonFilterMixin, Tool):
    """Batch multiple search queries for correlation analysis"""

    def __init__(self, toolset: "OpenSearchSearchToolset"):
        super().__init__(
            name="opensearch_msearch",
            description=(
                "Execute multiple OpenSearch queries in a single request using _msearch. "
                "Essential for correlation queries across different indices (logs, traces, metrics). "
                "Reduces latency by batching requests."
            ),
            parameters=self.extend_parameters({
                "searches": ToolParameter(
                    description=(
                        "Array of search specifications as stringified JSON. Each element should have "
                        "'index' and 'query' fields. Example: "
                        '[{"index": "logs-*", "query": {"term": {"trace.id": "abc"}}, "size": 100}, '
                        '{"index": "traces-*", "query": {"term": {"trace.id": "abc"}}, "size": 50}]'
                    ),
                    type="string",
                    required=True,
                ),
                "pit_id": ToolParameter(
                    description=(
                        "Optional Point-in-Time ID to use for all searches. "
                        "Ensures consistency across queries."
                    ),
                    type="string",
                    required=False,
                ),
            }),
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        err_msg = ""
        try:
            searches_str = get_param_or_raise(params, "searches")
            searches = json.loads(searches_str)

            if not isinstance(searches, list):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="searches parameter must be a JSON array",
                    params=params,
                )

            # Build _msearch request body (newline-delimited JSON)
            ndjson_lines = []
            pit_id = params.get("pit_id")

            for search_spec in searches:
                if not isinstance(search_spec, dict):
                    continue

                # Header line
                header: Dict[str, Any] = {}
                if pit_id:
                    # When using PIT, include it in header
                    header["pit"] = {"id": pit_id, "keep_alive": "5m"}
                else:
                    # Otherwise specify index
                    header["index"] = search_spec.get("index", "")

                ndjson_lines.append(json.dumps(header))

                # Body line
                body = {
                    "query": search_spec.get("query", {"match_all": {}}),
                    "size": search_spec.get("size", 100),
                    "track_total_hits": search_spec.get("track_total_hits", False),
                }

                if search_spec.get("_source"):
                    body["_source"] = search_spec["_source"]

                ndjson_lines.append(json.dumps(body))

            # Join with newlines and add trailing newline
            ndjson_body = "\n".join(ndjson_lines) + "\n"

            config = self._toolset.opensearch_config
            url = f"{config.opensearch_url}/_msearch"

            headers = {"Content-Type": "application/x-ndjson"}
            headers.update(add_auth_header(config.opensearch_auth_header))

            logging.debug(f"OpenSearch _msearch request: {ndjson_body}")

            response = requests.post(
                url=url,
                timeout=180,
                verify=True,
                data=ndjson_body,
                headers=headers,
            )

            if response.status_code > 300:
                err_msg = response.text

            response.raise_for_status()
            result = StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response.json(),
                params=params,
            )
            return self.filter_result(result, params)

        except requests.Timeout:
            logging.warning("Timeout while executing OpenSearch _msearch", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Request timed out while executing OpenSearch _msearch {err_msg}",
                params=params,
            )
        except RequestException as e:
            logging.warning("Failed to execute OpenSearch _msearch", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Network error while executing OpenSearch _msearch {err_msg}: {str(e)}",
                params=params,
            )
        except Exception as e:
            logging.warning("Failed to process OpenSearch _msearch", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error {err_msg}: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Multi-Search"


class OpenSearchPITOpen(Tool):
    """Open a Point-in-Time for consistent pagination"""

    def __init__(self, toolset: "OpenSearchSearchToolset"):
        super().__init__(
            name="opensearch_pit_open",
            description=(
                "Open a Point-in-Time (PIT) for consistent pagination across large result sets. "
                "PIT creates a snapshot of the index state for reliable deep pagination. "
                "Use the returned ID with opensearch_search."
            ),
            parameters={
                "index": ToolParameter(
                    description="Index pattern to create PIT for (e.g., 'logs-*')",
                    type="string",
                    required=True,
                ),
                "keep_alive": ToolParameter(
                    description="How long to keep PIT alive (e.g., '5m', '1h'). Default: '5m'",
                    type="string",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            index = get_param_or_raise(params, "index")
            keep_alive = params.get("keep_alive", "5m")

            config = self._toolset.opensearch_config
            url = f"{config.opensearch_url}/{index}/_pit?keep_alive={keep_alive}"

            headers = {}
            headers.update(add_auth_header(config.opensearch_auth_header))

            response = requests.post(
                url=url,
                timeout=30,
                verify=True,
                headers=headers,
            )

            response.raise_for_status()
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response.json(),
                params=params,
            )

        except requests.Timeout:
            logging.warning("Timeout while opening OpenSearch PIT", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Request timed out while opening OpenSearch PIT",
                params=params,
            )
        except RequestException as e:
            logging.warning("Failed to open OpenSearch PIT", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Network error while opening OpenSearch PIT: {str(e)}",
                params=params,
            )
        except Exception as e:
            logging.warning("Failed to process OpenSearch PIT open", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        index = params.get("index", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Open PIT ({index})"


class OpenSearchPITClose(Tool):
    """Close a Point-in-Time to free resources"""

    def __init__(self, toolset: "OpenSearchSearchToolset"):
        super().__init__(
            name="opensearch_pit_close",
            description="Close a Point-in-Time to free cluster resources. Good practice after pagination is complete.",
            parameters={
                "pit_id": ToolParameter(
                    description="The PIT ID to close",
                    type="string",
                    required=True,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            pit_id = get_param_or_raise(params, "pit_id")

            config = self._toolset.opensearch_config
            url = f"{config.opensearch_url}/_pit"

            headers = {"Content-Type": "application/json"}
            headers.update(add_auth_header(config.opensearch_auth_header))

            body = {"id": pit_id}

            response = requests.delete(
                url=url,
                timeout=30,
                verify=True,
                data=json.dumps(body),
                headers=headers,
            )

            response.raise_for_status()
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response.json(),
                params=params,
            )

        except requests.Timeout:
            logging.warning("Timeout while closing OpenSearch PIT", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Request timed out while closing OpenSearch PIT",
                params=params,
            )
        except RequestException as e:
            logging.warning("Failed to close OpenSearch PIT", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Network error while closing OpenSearch PIT: {str(e)}",
                params=params,
            )
        except Exception as e:
            logging.warning("Failed to process OpenSearch PIT close", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Close PIT"


class OpenSearchSearchToolset(BaseOpenSearchToolset):
    """Generalized OpenSearch search capabilities"""

    def __init__(self):
        super().__init__(
            name="opensearch/search",
            description=(
                "Comprehensive OpenSearch search capabilities including document retrieval, "
                "multi-search for correlations, aggregations, and PIT pagination."
            ),
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/opensearch-search/",
            icon_url="https://opensearch.org/assets/brand/PNG/Mark/opensearch_mark_default.png",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                OpenSearchSearch(toolset=self),
                OpenSearchMultiSearch(toolset=self),
                OpenSearchPITOpen(toolset=self),
                OpenSearchPITClose(toolset=self),
            ],
            tags=[
                ToolsetTag.CORE,
            ],
        )
        # Load generic search instructions
        template_file_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "opensearch_search_instructions.jinja2"
            )
        )
        if os.path.exists(template_file_path):
            self._load_llm_instructions(jinja_template=f"file://{template_file_path}")
