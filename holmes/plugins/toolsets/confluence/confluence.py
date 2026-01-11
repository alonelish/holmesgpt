import logging
import os
from typing import Any, Dict, Optional, Tuple

import requests
from pydantic import BaseModel, ConfigDict, model_validator
from requests.exceptions import SSLError

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


def format_ssl_error_message(confluence_url: str, error: SSLError) -> str:
    """Format a clear SSL error message with remediation steps."""
    return (
        f"SSL certificate verification failed when connecting to Confluence at {confluence_url}. "
        f"Error: {str(error)}. "
        f"To disable SSL verification, set 'verify_ssl: false' in your configuration. "
        f"For Helm deployments, add this to your values.yaml:\n"
        f"  toolsets:\n"
        f"    confluence:\n"
        f"      config:\n"
        f"        verify_ssl: false"
    )


class ConfluenceConfig(BaseModel):
    """Confluence toolset configuration.

    The toolset supports both config-based and environment variable-based configuration.
    If config values are not provided, it will fall back to environment variables:
    - CONFLUENCE_BASE_URL
    - CONFLUENCE_USER
    - CONFLUENCE_API_KEY
    """

    model_config = ConfigDict(extra="allow")

    base_url: Optional[str] = None
    user: Optional[str] = None
    api_key: Optional[str] = None
    verify_ssl: bool = True
    request_timeout_seconds: int = 30

    @model_validator(mode="after")
    def load_from_env_if_missing(self):
        """Load configuration from environment variables if not provided."""
        if not self.base_url:
            self.base_url = os.environ.get("CONFLUENCE_BASE_URL")
        if not self.user:
            self.user = os.environ.get("CONFLUENCE_USER")
        if not self.api_key:
            self.api_key = os.environ.get("CONFLUENCE_API_KEY")

        # Ensure base_url doesn't have a trailing slash
        if self.base_url and self.base_url.endswith("/"):
            self.base_url = self.base_url.rstrip("/")

        return self


class FetchConfluencePage(Tool):
    toolset: "ConfluenceToolset"

    def __init__(self, toolset: "ConfluenceToolset"):
        super().__init__(
            name="fetch_confluence_url",
            description=(
                "Fetch a page in Confluence. Use this to fetch Confluence runbooks "
                "if they are present before starting your investigation."
            ),
            parameters={
                "confluence_page_id": ToolParameter(
                    description="The ID of the Confluence page to fetch",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Confluence is not configured",
                params=params,
            )

        page_id = params.get("confluence_page_id")
        if not page_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="confluence_page_id parameter is required",
                params=params,
            )

        config = self.toolset.config
        url = f"{config.base_url}/wiki/rest/api/content/{page_id}"

        try:
            response = requests.get(
                url,
                auth=(config.user, config.api_key),  # type: ignore
                headers={"Content-Type": "application/json"},
                params={"expand": "body.storage"},
                timeout=config.request_timeout_seconds,
                verify=config.verify_ssl,
            )
            response.raise_for_status()
            data = response.json()

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )

        except SSLError as e:
            logging.warning("SSL error while fetching Confluence page", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=format_ssl_error_message(config.base_url or "", e),
                params=params,
            )
        except requests.exceptions.HTTPError as e:
            logging.warning(
                f"HTTP error while fetching Confluence page {page_id}", exc_info=True
            )
            error_msg = f"Failed to fetch Confluence page {page_id}: HTTP {e.response.status_code}"
            if e.response.status_code == 404:
                error_msg = f"Confluence page with ID {page_id} was not found"
            elif e.response.status_code == 401:
                error_msg = (
                    "Authentication failed. Please check your Confluence credentials"
                )
            elif e.response.status_code == 403:
                error_msg = f"Access denied to Confluence page {page_id}. Please check your permissions"
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
            )
        except requests.exceptions.Timeout:
            logging.warning(
                f"Timeout while fetching Confluence page {page_id}", exc_info=True
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Request timed out while fetching Confluence page {page_id}",
                params=params,
            )
        except requests.exceptions.RequestException as e:
            logging.warning(
                f"Request error while fetching Confluence page {page_id}", exc_info=True
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Network error while fetching Confluence page: {str(e)}",
                params=params,
            )
        except Exception as e:
            logging.warning(
                f"Unexpected error while fetching Confluence page {page_id}",
                exc_info=True,
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        page_id = params.get("confluence_page_id", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Fetch Page {page_id}"


class ConfluenceToolset(Toolset):
    config: Optional[ConfluenceConfig] = None

    def __init__(self):
        super().__init__(
            name="confluence",
            description="Fetch Confluence pages",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/confluence/",
            icon_url="https://platform.robusta.dev/demos/confluence.svg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                FetchConfluencePage(toolset=self),
            ],
            tags=[
                ToolsetTag.CORE,
            ],
        )

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        try:
            # Create config from provided dict (may be empty)
            self.config = ConfluenceConfig(**(config or {}))

            # Check if we have all required configuration
            if not self.config.base_url:
                return (
                    False,
                    "Confluence base URL is not configured. Set CONFLUENCE_BASE_URL environment variable or provide 'base_url' in config",
                )
            if not self.config.user:
                return (
                    False,
                    "Confluence user is not configured. Set CONFLUENCE_USER environment variable or provide 'user' in config",
                )
            if not self.config.api_key:
                return (
                    False,
                    "Confluence API key is not configured. Set CONFLUENCE_API_KEY environment variable or provide 'api_key' in config",
                )

            return self._health_check()

        except Exception as e:
            logging.exception("Failed to create Confluence config")
            return False, f"Failed to create Confluence config: {str(e)}"

    def _health_check(self) -> Tuple[bool, str]:
        """Verify connectivity to Confluence by making a simple API call."""
        if not self.config or not self.config.base_url:
            return False, "Confluence is not configured"

        # Use the /wiki/rest/api/space endpoint to verify connectivity
        # This is a lightweight call that doesn't require a specific page ID
        url = f"{self.config.base_url}/wiki/rest/api/space"

        try:
            response = requests.get(
                url,
                auth=(self.config.user, self.config.api_key),  # type: ignore
                headers={"Content-Type": "application/json"},
                params={"limit": 1},  # Only fetch 1 space to minimize load
                timeout=10,
                verify=self.config.verify_ssl,
            )

            if response.status_code == 200:
                return True, ""
            elif response.status_code == 401:
                return (
                    False,
                    "Authentication failed. Please check your Confluence credentials",
                )
            else:
                return (
                    False,
                    f"Failed to connect to Confluence: HTTP {response.status_code}",
                )

        except SSLError as e:
            return False, format_ssl_error_message(self.config.base_url, e)
        except Exception as e:
            logging.debug("Failed to initialize Confluence", exc_info=True)
            return (
                False,
                f"Failed to connect to Confluence at {self.config.base_url}: {str(e)}",
            )

    def get_example_config(self) -> Dict[str, Any]:
        example_config = ConfluenceConfig(
            base_url="https://your-domain.atlassian.net",
            user="your-email@example.com",
            api_key="your-api-key",
            verify_ssl=True,
        )
        return example_config.model_dump(exclude_none=True)
