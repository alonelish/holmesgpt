import logging
import os
from abc import ABC
from typing import Any, ClassVar, Dict, Literal, Optional, Tuple, Type, cast

import requests  # type: ignore
from pydantic import ConfigDict, Field, model_validator

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
from holmes.plugins.toolsets.confluence.converter import confluence_storage_to_markdown
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)


class ConfluenceConfig(ToolsetConfig):
    """Configuration for Confluence REST API access.

    Supports both Confluence Cloud and Data Center/Server.

    Cloud example:
    ```yaml
    toolsets:
      confluence:
        config:
          api_url: "https://mycompany.atlassian.net"
          user: "user@example.com"
          api_key: "your-atlassian-api-token"
    ```

    Data Center with Personal Access Token:
    ```yaml
    toolsets:
      confluence:
        config:
          api_url: "https://confluence.mycompany.com"
          api_key: "your-personal-access-token"
          auth_type: "bearer"
          api_path_prefix: ""
    ```
    """

    model_config = ConfigDict(extra="allow")

    api_url: str = Field(
        title="API URL",
        description="Confluence base URL (e.g., https://mycompany.atlassian.net for Cloud, https://confluence.mycompany.com for Data Center)",
        examples=["https://mycompany.atlassian.net", "https://confluence.mycompany.com"],
    )
    user: Optional[str] = Field(
        default=None,
        title="User",
        description="Confluence user email (Cloud) or username (Data Center). Required for basic auth, not needed for bearer auth.",
        examples=["user@example.com"],
    )
    api_key: str = Field(
        title="API Key",
        description="Atlassian API token (Cloud) or Personal Access Token (Data Center)",
    )
    auth_type: Literal["basic", "bearer"] = Field(
        default="basic",
        title="Auth Type",
        description="Authentication type: 'basic' for Cloud (user + API token) or Data Center (user + password), 'bearer' for Data Center Personal Access Tokens (PAT).",
    )
    api_path_prefix: str = Field(
        default="/wiki",
        title="API Path Prefix",
        description="Path prefix before /rest/api. Cloud uses '/wiki' (default). Data Center typically uses '' (empty string). Set to match your instance's context path.",
        examples=["/wiki", "", "/confluence"],
    )

    @model_validator(mode="after")
    def handle_deprecated_fields(self) -> "ConfluenceConfig":
        extra = self.model_extra or {}
        deprecated = []

        # Support old env var naming convention
        if "base_url" in extra and not self.api_url:
            self.api_url = extra["base_url"]
            deprecated.append("base_url -> api_url")

        if deprecated:
            logging.warning(f"Deprecated Confluence config names: {', '.join(deprecated)}")

        return self

    @model_validator(mode="after")
    def validate_auth(self) -> "ConfluenceConfig":
        if self.auth_type == "basic" and not self.user:
            raise ValueError(
                "Confluence 'user' is required when auth_type is 'basic'. "
                "For Data Center Personal Access Tokens, set auth_type to 'bearer'."
            )
        return self


class ConfluenceToolset(Toolset):
    config_classes: ClassVar[list[Type[ConfluenceConfig]]] = [ConfluenceConfig]

    def __init__(self) -> None:
        super().__init__(
            name="confluence",
            description="Fetch and search Confluence pages",
            icon_url="https://platform.robusta.dev/demos/confluence.svg",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/confluence/",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                GetPage(self),
                SearchPages(self),
            ],
            tags=[ToolsetTag.CORE],
        )

        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        try:
            self.config = ConfluenceConfig(**config)
            return self._perform_health_check()
        except Exception as e:
            return False, f"Failed to validate Confluence configuration: {e}"

    def _perform_health_check(self) -> Tuple[bool, str]:
        try:
            data = self.make_request("/rest/api/space", query_params={"limit": "1"})
            return True, "Confluence API is accessible."
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            if status == 401:
                return False, f"Confluence authentication failed. Check user/api_key. HTTP {status}: {e.response.text}"
            if status == 403:
                return False, f"Confluence access denied. Check permissions. HTTP {status}: {e.response.text}"
            return False, f"Confluence API error: HTTP {status}: {e.response.text}"
        except requests.exceptions.ConnectionError as e:
            return False, f"Failed to connect to Confluence at {self.confluence_config.api_url}: {e}"
        except requests.exceptions.Timeout:
            return False, "Confluence health check timed out"
        except Exception as e:
            return False, f"Confluence health check failed: {e}"

    @property
    def confluence_config(self) -> ConfluenceConfig:
        return cast(ConfluenceConfig, self.config)

    def _build_url(self, path: str, params: Optional[Dict[str, str]] = None) -> str:
        base = self.confluence_config.api_url.rstrip("/")
        prefix = self.confluence_config.api_path_prefix.rstrip("/")
        url = f"{base}{prefix}{path}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{query}"
        return url

    def _build_auth_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Accept": "application/json"}
        if self.confluence_config.auth_type == "bearer":
            headers["Authorization"] = f"Bearer {self.confluence_config.api_key}"
        return headers

    def _build_auth_tuple(self) -> Optional[Tuple[str, str]]:
        if self.confluence_config.auth_type == "basic":
            return (self.confluence_config.user or "", self.confluence_config.api_key)
        return None

    def make_request(
        self,
        path: str,
        query_params: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        url = self._build_url(path, query_params)
        response = requests.get(
            url,
            auth=self._build_auth_tuple(),
            headers=self._build_auth_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()


class BaseConfluenceTool(Tool, ABC):
    def __init__(self, toolset: ConfluenceToolset, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    def _convert_body(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """If the response contains body.storage.value, convert it to markdown."""
        body = data.get("body", {})
        storage = body.get("storage", {})
        html = storage.get("value")
        if html:
            data["body_markdown"] = confluence_storage_to_markdown(html)
            # Remove raw storage format to save tokens
            del data["body"]
        return data

    def _strip_metadata(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Remove bulky metadata fields that waste LLM tokens."""
        for key in ("_links", "_expandable", "extensions", "metadata"):
            data.pop(key, None)
        return data


class GetPage(BaseConfluenceTool):
    def __init__(self, toolset: ConfluenceToolset) -> None:
        super().__init__(
            toolset=toolset,
            name="confluence_get_page",
            description="Get a Confluence page by its content ID. Returns the page title, body content (converted to markdown), and metadata.",
            parameters={
                "content_id": ToolParameter(
                    description="The numeric content ID of the Confluence page. This can be found in the page URL (e.g., /pages/12345/Page+Title) or from search results.",
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        content_id = params["content_id"]
        try:
            data = self._toolset.make_request(
                f"/rest/api/content/{content_id}",
                query_params={"expand": "body.storage,version,space"},
            )
        except requests.exceptions.HTTPError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to get Confluence page {content_id}: HTTP {e.response.status_code}: {e.response.text}",
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to get Confluence page {content_id}: {e}",
                params=params,
            )

        data = self._convert_body(data)
        data = self._strip_metadata(data)

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            params=params,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        content_id = params.get("content_id", "unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Confluence page {content_id}"


class SearchPages(BaseConfluenceTool):
    def __init__(self, toolset: ConfluenceToolset) -> None:
        super().__init__(
            toolset=toolset,
            name="confluence_search",
            description=(
                "Search Confluence pages using CQL (Confluence Query Language). "
                "Returns matching pages with titles and content excerpts. "
                "Use expand_body=true to also fetch and convert the full body of each result."
            ),
            parameters={
                "cql": ToolParameter(
                    description=(
                        "CQL query string. Examples: "
                        'title="Page Title", '
                        'text~"search term", '
                        'space=SPACEKEY AND title~"keyword", '
                        'label="runbook" AND space=SRE'
                    ),
                    type="string",
                    required=True,
                ),
                "limit": ToolParameter(
                    description="Maximum number of results to return (default: 10, max: 50)",
                    type="integer",
                    required=False,
                ),
                "expand_body": ToolParameter(
                    description="If true, fetch and convert the full page body for each result. Use sparingly — increases response size. Default: false.",
                    type="boolean",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        cql = params["cql"]
        limit = min(params.get("limit", 10), 50)
        expand_body = params.get("expand_body", False)

        expand = "version,space"
        if expand_body:
            expand += ",body.storage"

        try:
            data = self._toolset.make_request(
                "/rest/api/content/search",
                query_params={
                    "cql": cql,
                    "limit": str(limit),
                    "expand": expand,
                },
            )
        except requests.exceptions.HTTPError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Confluence search failed for CQL '{cql}': HTTP {e.response.status_code}: {e.response.text}",
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Confluence search failed for CQL '{cql}': {e}",
                params=params,
            )

        results = []
        for item in data.get("results", []):
            item = self._strip_metadata(item)
            if expand_body:
                item = self._convert_body(item)
            results.append(item)

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={
                "total_size": data.get("totalSize", len(results)),
                "results": results,
            },
            params=params,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        cql = params.get("cql", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Search Confluence '{cql}'"
