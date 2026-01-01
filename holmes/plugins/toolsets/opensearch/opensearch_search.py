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
    """Thin wrapper for OpenSearch /<index>/_search API"""

    def __init__(self, toolset: "OpenSearchSearchToolset"):
        super().__init__(
            name="opensearch_search",
            description=(
                "Execute OpenSearch _search API: POST /<index>/_search or POST /_search with PIT. "
                "Thin wrapper - accepts full OpenSearch query DSL request body. "
                "Supports all search parameters: query, size, from, sort, _source, aggregations, "
                "track_total_hits, timeout, search_after, pit, etc."
            ),
            parameters=self.extend_parameters({
                "index": ToolParameter(
                    description=(
                        "Index pattern to search (e.g., 'logs-*', 'traces-*'). "
                        "Omit when using PIT (pit parameter in body)"
                    ),
                    type="string",
                    required=False,
                ),
                "body": ToolParameter(
                    description=(
                        "Complete OpenSearch _search request body as stringified JSON. "
                        "Full OpenSearch Query DSL supported. Examples:\n"
                        '{"query": {"bool": {"filter": [{"range": {"@timestamp": {"gte": "now-1h"}}}]}}, "size": 100}\n'
                        '{"query": {"match_all": {}}, "_source": ["@timestamp", "message"], "size": 50}\n'
                        '{"query": {"term": {"level": "ERROR"}}, "aggs": {"by_service": {"terms": {"field": "service.keyword"}}}}\n'
                        '{"query": {"match_all": {}}, "pit": {"id": "pit_id", "keep_alive": "1m"}, "sort": [...], "search_after": [...]}'
                    ),
                    type="string",
                    required=True,
                ),
            }),
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        err_msg = ""
        try:
            # Parse the request body
            body_str = get_param_or_raise(params, "body")
            body = json.loads(body_str)

            # Determine the URL
            config = self._toolset.opensearch_config
            
            # Check if PIT is used in the body
            if body.get("pit"):
                # When using PIT, don't include index in URL
                url = f"{config.opensearch_url}/_search"
            else:
                # Otherwise, index is required
                index = params.get("index")
                if not index:
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.ERROR,
                        error="'index' parameter required when not using PIT in body",
                        params=params,
                    )
                url = f"{config.opensearch_url}/{index}/_search"

            headers = {"Content-Type": "application/json"}
            headers.update(add_auth_header(config.opensearch_auth_header))

            logging.debug(f"OpenSearch _search: {url} body={json.dumps(body)}")

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
    """Thin wrapper for OpenSearch /_msearch API"""

    def __init__(self, toolset: "OpenSearchSearchToolset"):
        super().__init__(
            name="opensearch_msearch",
            description=(
                "Execute OpenSearch _msearch API: POST /_msearch. "
                "Thin wrapper - accepts newline-delimited JSON (ndjson) format. "
                "Each search requires two lines: header line (with index or PIT) and body line (with query). "
                "Useful for executing multiple searches in one request for correlation analysis."
            ),
            parameters=self.extend_parameters({
                "ndjson": ToolParameter(
                    description=(
                        "Newline-delimited JSON string. Format: header\\nbody\\nheader\\nbody\\n. "
                        "Each header line must contain 'index' OR 'pit'. Each body line is a search request. "
                        "Example:\n"
                        '{\"index\": \"logs-*\"}\\n{\"query\": {\"term\": {\"trace.id\": \"abc\"}}, \"size\": 100}\\n'
                        '{\"index\": \"traces-*\"}\\n{\"query\": {\"term\": {\"trace.id\": \"abc\"}}, \"size\": 50}\\n'
                    ),
                    type="string",
                    required=True,
                ),
            }),
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        err_msg = ""
        try:
            ndjson_body = get_param_or_raise(params, "ndjson")
            
            # Ensure trailing newline
            if not ndjson_body.endswith("\n"):
                ndjson_body += "\n"

            config = self._toolset.opensearch_config
            url = f"{config.opensearch_url}/_msearch"

            headers = {"Content-Type": "application/x-ndjson"}
            headers.update(add_auth_header(config.opensearch_auth_header))

            logging.debug(f"OpenSearch _msearch: {url}")

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
    """Thin wrappers for OpenSearch REST APIs"""

    def __init__(self):
        super().__init__(
            name="opensearch/search",
            description=(
                "Thin wrappers for OpenSearch search APIs: _search, _msearch, _pit. "
                "Accepts full OpenSearch Query DSL - all parameters exposed."
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
