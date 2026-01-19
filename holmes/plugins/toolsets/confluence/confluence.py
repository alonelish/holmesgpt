from typing import Any, Dict, Optional, Tuple

import requests
from pydantic import BaseModel, ConfigDict

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
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class ConfluenceConfig(BaseModel):
    """Configuration for Confluence API access.

    Supports two authentication methods:

    1. Basic Auth (for Atlassian Cloud - most common):
    ```yaml
    url: "https://your-company.atlassian.net"
    username: "your-email@example.com"
    api_key: "your_api_token"
    ```

    2. Bearer Token (for self-hosted or PAT):
    ```yaml
    url: "https://confluence.your-company.com"
    api_key: "your_bearer_token"
    # Note: no username = Bearer auth
    ```
    """

    model_config = ConfigDict(extra="allow")

    url: str
    api_key: str
    username: Optional[str] = None  # If set, uses Basic Auth; otherwise Bearer
    verify_ssl: bool = True
    timeout: int = 30


class ConfluenceToolset(Toolset):
    """Toolset for accessing Confluence pages and spaces."""

    def __init__(self):
        super().__init__(
            name="confluence",
            enabled=False,
            description="Fetch Confluence pages and spaces",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/confluence/",
            icon_url="https://platform.robusta.dev/demos/confluence.svg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            tags=[ToolsetTag.CORE],
        )
        self.tools = [
            ListConfluenceSpaces(self),
            GetConfluenceSpacePages(self),
            FetchConfluencePage(self),
        ]

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if the Confluence configuration is valid and accessible."""
        try:
            self.config = ConfluenceConfig(**config)
            return self._perform_health_check()
        except Exception as e:
            return False, f"Failed to validate Confluence configuration: {str(e)}"

    def _perform_health_check(self) -> Tuple[bool, str]:
        """Perform a health check by querying the space list."""
        try:
            response = self._make_request("GET", "wiki/rest/api/space", params={"limit": 1})
            if "results" in response:
                return True, "Connected to Confluence successfully"
            return False, "Unexpected response from Confluence API"
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                return False, "Confluence authentication failed. Check your API key."
            elif e.response.status_code == 403:
                return False, "Confluence access denied. Ensure your API key has read permissions."
            else:
                return False, f"Confluence API error: {e.response.status_code} - {e.response.text}"
        except requests.exceptions.ConnectionError:
            return False, f"Failed to connect to Confluence at {self.confluence_config.url}"
        except requests.exceptions.Timeout:
            return False, "Confluence health check timed out"
        except Exception as e:
            return False, f"Confluence health check failed: {str(e)}"

    @property
    def confluence_config(self) -> ConfluenceConfig:
        return self.config  # type: ignore

    def get_example_config(self) -> Dict[str, Any]:
        """Return an example configuration for this toolset."""
        return {
            "url": "https://your-company.atlassian.net",
            "username": "{{ env.CONFLUENCE_USERNAME }}",
            "api_key": "{{ env.CONFLUENCE_API_KEY }}",
            "verify_ssl": True,
        }

    def _get_headers(self) -> Dict[str, str]:
        """Build request headers."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        # If no username, use Bearer auth
        if not self.confluence_config.username:
            headers["Authorization"] = f"Bearer {self.confluence_config.api_key}"
        return headers

    def _get_auth(self) -> Optional[Tuple[str, str]]:
        """Return Basic Auth tuple if username is configured."""
        if self.confluence_config.username:
            return (self.confluence_config.username, self.confluence_config.api_key)
        return None

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Make HTTP request to Confluence API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., "wiki/rest/api/space")
            params: Query parameters
            body: Request body (JSON)
            timeout: Request timeout in seconds

        Returns:
            Parsed JSON response

        Raises:
            requests.exceptions.HTTPError: For HTTP error responses
        """
        url = f"{self.confluence_config.url.rstrip('/')}/{endpoint.lstrip('/')}"
        timeout = timeout or self.confluence_config.timeout

        response = requests.request(
            method=method,
            url=url,
            headers=self._get_headers(),
            auth=self._get_auth(),
            params=params,
            json=body,
            timeout=timeout,
            verify=self.confluence_config.verify_ssl,
        )
        response.raise_for_status()
        return response.json()


class BaseConfluenceTool(Tool):
    """Base class for Confluence tools."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, toolset: ConfluenceToolset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    @property
    def toolset(self) -> ConfluenceToolset:
        return self._toolset

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: dict,
        query_params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> StructuredToolResult:
        """Make a request to Confluence and return structured result."""
        try:
            data = self._toolset._make_request(
                method=method,
                endpoint=endpoint,
                params=query_params,
                body=body,
                timeout=timeout,
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except requests.exceptions.HTTPError as e:
            error_detail = f"HTTP {e.response.status_code}"
            try:
                error_body = e.response.json()
                if "message" in error_body:
                    error_detail = f"{error_detail}: {error_body['message']}"
            except Exception:
                error_detail = f"{error_detail}: {e.response.text[:500]}"

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Confluence request failed for endpoint '{endpoint}': {error_detail}",
                params=params,
            )
        except requests.exceptions.Timeout:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Confluence request timed out for endpoint '{endpoint}'",
                params=params,
            )
        except requests.exceptions.ConnectionError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to connect to Confluence: {str(e)}",
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error querying Confluence: {str(e)}",
                params=params,
            )


class ListConfluenceSpaces(BaseConfluenceTool):
    """List all spaces in Confluence."""

    def __init__(self, toolset: ConfluenceToolset):
        super().__init__(
            toolset=toolset,
            name="list_confluence_spaces",
            description=(
                "List all spaces in Confluence. Returns space keys, names, and types. "
                "Use the space_key from results to get pages within a specific space."
            ),
            parameters={
                "limit": ToolParameter(
                    description="Maximum number of spaces to return (default: 100)",
                    type="integer",
                    required=False,
                ),
                "start": ToolParameter(
                    description="Starting index for pagination (default: 0)",
                    type="integer",
                    required=False,
                ),
                "type": ToolParameter(
                    description="Filter by space type: 'global' or 'personal'",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        query_params: Dict[str, Any] = {
            "limit": params.get("limit", 100),
            "start": params.get("start", 0),
        }

        if params.get("type"):
            query_params["type"] = params["type"]

        return self._make_request("GET", "wiki/rest/api/space", params, query_params=query_params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List spaces"


class GetConfluenceSpacePages(BaseConfluenceTool):
    """Get pages within a specific Confluence space."""

    def __init__(self, toolset: ConfluenceToolset):
        super().__init__(
            toolset=toolset,
            name="get_confluence_space_pages",
            description=(
                "Get pages within a specific Confluence space. Returns page IDs, titles, and metadata. "
                "Use the page ID with fetch_confluence_page to get full page content."
            ),
            parameters={
                "space_key": ToolParameter(
                    description="The space key (e.g., 'DEV', 'DOCS'). Use list_confluence_spaces to find available space keys.",
                    type="string",
                    required=True,
                ),
                "limit": ToolParameter(
                    description="Maximum number of pages to return (default: 25, max: 100)",
                    type="integer",
                    required=False,
                ),
                "start": ToolParameter(
                    description="Starting index for pagination (default: 0)",
                    type="integer",
                    required=False,
                ),
                "title": ToolParameter(
                    description="Filter pages by title (partial match)",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        space_key = params["space_key"]

        query_params: Dict[str, Any] = {
            "limit": params.get("limit", 25),
            "start": params.get("start", 0),
            "expand": "metadata.labels",
        }

        if params.get("title"):
            query_params["title"] = params["title"]

        endpoint = f"wiki/rest/api/space/{space_key}/content/page"
        return self._make_request("GET", endpoint, params, query_params=query_params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        space_key = params.get("space_key", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Pages in {space_key}"


class FetchConfluencePage(BaseConfluenceTool):
    """Fetch a Confluence page by its ID."""

    def __init__(self, toolset: ConfluenceToolset):
        super().__init__(
            toolset=toolset,
            name="fetch_confluence_page",
            description=(
                "Fetch a Confluence page by its page ID. Returns full page content including body. "
                "Use get_confluence_space_pages to find page IDs within a space."
            ),
            parameters={
                "page_id": ToolParameter(
                    description="The Confluence page ID. Use get_confluence_space_pages to find page IDs.",
                    type="string",
                    required=True,
                ),
                "expand": ToolParameter(
                    description=(
                        "Comma-separated list of properties to expand. "
                        "Default: 'body.storage'. Options: body.storage, body.view, version, ancestors, children, history"
                    ),
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        page_id = params["page_id"]
        expand = params.get("expand", "body.storage")

        query_params = {"expand": expand}

        endpoint = f"wiki/rest/api/content/{page_id}"
        return self._make_request("GET", endpoint, params, query_params=query_params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        page_id = params.get("page_id", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Fetch page {page_id}"
