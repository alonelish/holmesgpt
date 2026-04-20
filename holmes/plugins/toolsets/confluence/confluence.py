import logging
import re
from typing import Any, ClassVar, Dict, List, Literal, Optional, Tuple, Type
from urllib.parse import urlparse

import requests  # type: ignore
from pydantic import Field, model_validator

from holmes.core.tools import CallablePrerequisite, Toolset, ToolsetTag
from holmes.plugins.toolsets.http.http_toolset import (
    AuthConfig,
    EndpointConfig,
    HttpToolset,
    HttpToolsetConfig,
)
from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)

ATLASSIAN_CLOUD_PATTERN = re.compile(r"https?://[^/]+\.atlassian\.net")
ATLASSIAN_GATEWAY_BASE = "https://api.atlassian.com/ex/confluence"
CONFLUENCE_ICON_URL = (
    "https://raw.githubusercontent.com/gilbarbara/logos/"
    "de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/confluence.svg"
)


class ConfluenceConfig(ToolsetConfig):
    """Base configuration shared by all Confluence variants (Cloud and Data Center).

    Holds the full runtime surface. Variant subclasses (`ConfluenceCloudConfig`,
    `ConfluenceDataCenterPATConfig`, `ConfluenceDataCenterBasicConfig`) narrow
    and rename the fields shown to the frontend; runtime code keeps reading
    the same attribute names off whichever subclass was instantiated.
    """

    api_url: str = Field(
        title="URL",
        description="Confluence base URL",
        examples=["https://mycompany.atlassian.net"],
    )
    user: Optional[str] = Field(
        default=None,
        title="User",
        description="User email (Cloud) or username (Data Center). Required for basic auth.",
    )
    api_key: str = Field(
        title="API Key",
        description="Atlassian API token (Cloud), Personal Access Token (Data Center), or password",
        examples=["{{ env.CONFLUENCE_API_KEY }}"],
        json_schema_extra={"format": "password"},
    )
    auth_type: Literal["basic", "bearer"] = Field(
        default="basic",
        title="Authentication Type",
        description="'basic' for Cloud or Data Center user+password, 'bearer' for Data Center PATs.",
    )
    api_path_prefix: str = Field(
        default="/wiki",
        title="API Path Prefix",
        description="Path prefix before /rest/api. Cloud uses '/wiki', Data Center typically ''.",
    )
    cloud_id: Optional[str] = Field(
        default=None,
        title="Cloud ID",
        description="Atlassian Cloud ID for the API gateway. Auto-detected when needed.",
    )

    @model_validator(mode="after")
    def validate_auth(self) -> "ConfluenceConfig":
        if self.auth_type == "basic" and not self.user:
            raise ValueError(
                "'user' is required when auth_type is 'basic'. For PATs, set auth_type to 'bearer'."
            )
        return self


class ConfluenceCloudConfig(ConfluenceConfig):
    """Confluence Cloud — hosted at yourcompany.atlassian.net with an API token."""

    _name: ClassVar[Optional[str]] = "Confluence Cloud"
    _description: ClassVar[Optional[str]] = (
        "Confluence Cloud hosted at <your-company>.atlassian.net, authenticated with an API token."
    )
    _icon_url: ClassVar[Optional[str]] = CONFLUENCE_ICON_URL
    _docs_anchor: ClassVar[Optional[str]] = "confluence-cloud"
    _hidden_fields: ClassVar[List[str]] = [
        "auth_type",
        "api_path_prefix",
        "cloud_id",
    ]
    _recommended: ClassVar[bool] = True

    api_url: str = Field(  # type: ignore[assignment]
        title="Confluence URL",
        description="Your Confluence Cloud URL",
        examples=["https://yourcompany.atlassian.net"],
    )
    user: str = Field(  # type: ignore[assignment]
        title="User Email",
        description="Email address of the Atlassian user whose API token you're using",
        examples=["you@yourcompany.com"],
    )
    api_key: str = Field(  # type: ignore[assignment]
        title="API Token",
        description=(
            "Atlassian API token. Create one at "
            "https://id.atlassian.com/manage/api-tokens"
        ),
        examples=["{{ env.CONFLUENCE_API_KEY }}"],
        json_schema_extra={"format": "password"},
    )


class ConfluenceDataCenterPATConfig(ConfluenceConfig):
    """Confluence Data Center / Server authenticated with a Personal Access Token."""

    _name: ClassVar[Optional[str]] = "Confluence Data Center - Personal Access Token"
    _description: ClassVar[Optional[str]] = (
        "Self-hosted Confluence Data Center / Server authenticated with a Personal Access Token (recommended for DC)."
    )
    _icon_url: ClassVar[Optional[str]] = CONFLUENCE_ICON_URL
    _docs_anchor: ClassVar[Optional[str]] = "confluence-data-center-personal-access-token"
    _hidden_fields: ClassVar[List[str]] = [
        "user",
        "auth_type",
        "api_path_prefix",
        "cloud_id",
    ]

    api_url: str = Field(  # type: ignore[assignment]
        title="Confluence URL",
        description="Base URL of your self-hosted Confluence instance",
        examples=["https://confluence.yourcompany.com"],
    )
    api_key: str = Field(  # type: ignore[assignment]
        title="Personal Access Token",
        description=(
            "Personal Access Token. Create one in Confluence at "
            "Profile → Personal Access Tokens → Create token."
        ),
        examples=["{{ env.CONFLUENCE_PAT }}"],
        json_schema_extra={"format": "password"},
    )
    auth_type: Literal["basic", "bearer"] = Field(  # type: ignore[assignment]
        default="bearer",
        title="Authentication Type",
        description="Always 'bearer' for Personal Access Tokens.",
    )
    api_path_prefix: str = Field(  # type: ignore[assignment]
        default="",
        title="API Path Prefix",
        description="Data Center uses an empty path prefix.",
    )


class ConfluenceDataCenterBasicConfig(ConfluenceConfig):
    """Confluence Data Center / Server authenticated with username + password."""

    _name: ClassVar[Optional[str]] = "Confluence Data Center - Basic Auth"
    _description: ClassVar[Optional[str]] = (
        "Self-hosted Confluence Data Center / Server authenticated with a username and password."
    )
    _icon_url: ClassVar[Optional[str]] = CONFLUENCE_ICON_URL
    _docs_anchor: ClassVar[Optional[str]] = "confluence-data-center-basic-auth"
    _hidden_fields: ClassVar[List[str]] = [
        "auth_type",
        "api_path_prefix",
        "cloud_id",
    ]

    api_url: str = Field(  # type: ignore[assignment]
        title="Confluence URL",
        description="Base URL of your self-hosted Confluence instance",
        examples=["https://confluence.yourcompany.com"],
    )
    user: str = Field(  # type: ignore[assignment]
        title="Username",
        description="Confluence Data Center username",
        examples=["myuser"],
    )
    api_key: str = Field(  # type: ignore[assignment]
        title="Password",
        description="Confluence Data Center password for the user above",
        examples=["{{ env.CONFLUENCE_PASSWORD }}"],
        json_schema_extra={"format": "password"},
    )
    api_path_prefix: str = Field(  # type: ignore[assignment]
        default="",
        title="API Path Prefix",
        description="Data Center uses an empty path prefix.",
    )


def determine_confluence_class(config: Dict[str, Any]) -> Type[ConfluenceConfig]:
    """Pick the right variant based on the config dict.

    - URL matching *.atlassian.net → Confluence Cloud
    - Otherwise auth_type='bearer' (or 'bearer' in config hints) → Data Center PAT
    - Otherwise → Data Center Basic Auth
    """
    api_url = str(config.get("api_url", "") or "")
    if ATLASSIAN_CLOUD_PATTERN.match(api_url):
        return ConfluenceCloudConfig
    if str(config.get("auth_type", "") or "").lower() == "bearer":
        return ConfluenceDataCenterPATConfig
    return ConfluenceDataCenterBasicConfig


class ConfluenceToolset(Toolset):
    """Confluence toolset that auto-detects auth and delegates to the HTTP toolset."""

    # Order matters: frontend shows them in this order, and
    # `prerequisites_callable` picks via `determine_confluence_class` below.
    # The first entry is the recommended default.
    config_classes: ClassVar[list[Type[ConfluenceConfig]]] = [
        ConfluenceCloudConfig,
        ConfluenceDataCenterPATConfig,
        ConfluenceDataCenterBasicConfig,
    ]

    def __init__(self) -> None:
        super().__init__(
            name="confluence",
            description="Fetch and search Confluence pages",
            icon_url=CONFLUENCE_ICON_URL,
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/confluence/",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            tags=[ToolsetTag.CORE],
        )
        self._gateway_base_url: Optional[str] = None

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        try:
            # Pick the variant based on the supplied config. Backwards-compatible
            # with existing YAML: if `api_url` looks like Atlassian Cloud, use
            # the Cloud variant; else use Data Center PAT or Basic based on
            # auth_type. All three variants share the same runtime contract
            # (same attribute names) so the rest of this class is unchanged.
            config_cls = determine_confluence_class(config)
            self.config = config_cls(**config)
            self._gateway_base_url = None

            ok, msg = self._perform_health_check()
            if not ok:
                return False, msg

            self._setup_http_tools()
            return True, msg
        except Exception as e:
            return False, f"Failed to validate Confluence configuration: {e}"

    @property
    def _conf(self) -> ConfluenceConfig:
        return self.config  # type: ignore[return-value]

    # ── Cloud detection & gateway ──

    def _is_cloud_url(self) -> bool:
        return bool(ATLASSIAN_CLOUD_PATTERN.match(self._conf.api_url))

    def _resolve_cloud_id(self) -> Optional[str]:
        if self._conf.cloud_id:
            return self._conf.cloud_id
        try:
            resp = requests.get(f"{self._conf.api_url.rstrip('/')}/_edge/tenant_info", timeout=10)
            resp.raise_for_status()
            cloud_id = resp.json().get("cloudId")
            if cloud_id:
                logger.info("Resolved Atlassian Cloud ID: %s", cloud_id)
            return cloud_id
        except Exception as e:
            logger.debug("Failed to resolve Cloud ID: %s", e)
            return None

    def _activate_gateway(self, cloud_id: str) -> None:
        self._gateway_base_url = f"{ATLASSIAN_GATEWAY_BASE}/{cloud_id}"
        logger.info("Using Atlassian API gateway: %s", self._gateway_base_url)

    # ── Health check ──

    def _probe_request(self, path: str, query_params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Direct HTTP request for health-check probing."""
        base = (self._gateway_base_url or self._conf.api_url).rstrip("/")
        prefix = self._conf.api_path_prefix.rstrip("/")
        url = f"{base}{prefix}{path}"

        headers: Dict[str, str] = {"Accept": "application/json"}
        auth: Optional[Tuple[str, str]] = None
        if self._conf.auth_type == "bearer" or self._gateway_base_url:
            headers["Authorization"] = f"Bearer {self._conf.api_key}"
        else:
            auth = (self._conf.user or "", self._conf.api_key)

        response = requests.get(url, params=query_params, auth=auth, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def _perform_health_check(self) -> Tuple[bool, str]:
        if self._conf.cloud_id and self._is_cloud_url():
            self._activate_gateway(self._conf.cloud_id)

        try:
            self._probe_request("/rest/api/space", query_params={"limit": "1"})
            return True, "Confluence API is accessible."
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            if status in (401, 403) and self._is_cloud_url() and not self._gateway_base_url:
                ok, msg = self._try_gateway_fallback()
                if ok:
                    return True, msg
            return False, f"Confluence API error: HTTP {status}: {e.response.text}"
        except requests.exceptions.ConnectionError as e:
            return False, f"Failed to connect to Confluence at {self._conf.api_url}: {e}"
        except requests.exceptions.Timeout:
            return False, "Confluence health check timed out"
        except Exception as e:
            return False, f"Confluence health check failed: {e}"

    def _try_gateway_fallback(self) -> Tuple[bool, str]:
        cloud_id = self._resolve_cloud_id()
        if not cloud_id:
            return False, "Could not resolve Cloud ID for gateway fallback."

        self._activate_gateway(cloud_id)
        try:
            self._probe_request("/rest/api/space", query_params={"limit": "1"})
            return True, "Confluence API is accessible via Atlassian API gateway (scoped token)."
        except Exception as e:
            self._gateway_base_url = None
            return False, f"Confluence API gateway fallback failed: {e}"

    # ── HTTP toolset delegation ──

    def _effective_base(self) -> str:
        return (self._gateway_base_url or self._conf.api_url).rstrip("/")

    def _build_endpoint_config(self) -> EndpointConfig:
        effective_url = self._effective_base()
        prefix = self._conf.api_path_prefix.rstrip("/")
        parsed = urlparse(effective_url)
        host = parsed.hostname or parsed.netloc
        root = parsed.path.rstrip("/")

        if self._conf.auth_type == "bearer" or self._gateway_base_url:
            auth = AuthConfig(type="bearer", token=self._conf.api_key)
        else:
            auth = AuthConfig(type="basic", username=self._conf.user or "", password=self._conf.api_key)

        return EndpointConfig(
            hosts=[host],
            paths=[f"{root}{prefix}/rest/api/*"],
            methods=["GET"],
            auth=auth,
            health_check_url=f"{parsed.scheme}://{parsed.netloc}{root}{prefix}/rest/api/space?limit=1",
        )

    def _build_llm_instructions(self) -> str:
        base = f"{self._effective_base()}{self._conf.api_path_prefix.rstrip('/')}"
        api = f"{base}/rest/api"
        return f"""### Confluence REST API

Base URL: {base}

**Endpoints:**

- GET {api}/space - List spaces (params: limit, start, type, status)
- GET {api}/space/{{spaceKey}} - Get space details
- GET {api}/content/{{contentId}}?expand=body.storage - Get page by ID
- GET {api}/content/{{contentId}}?expand=ancestors - Get page with parent hierarchy
- GET {api}/content/{{contentId}}/child/page?expand=body.storage - Get child pages
- GET {api}/content/{{contentId}}/child/comment?expand=body.storage - Get comments
- GET {api}/content/search?cql={{query}}&expand=body.storage - Search using CQL
- GET {api}/content?title={{title}}&spaceKey={{spaceKey}}&type=page&expand=body.storage - Find page by title

**CQL examples:** `title="Page Title"`, `text~"search term"`, `space=OPS AND label="runbook"`

**Page IDs from URLs:** `https://company.atlassian.net/wiki/spaces/SPACE/pages/12345/Title` → content ID is `12345`

**Tips:** Always use `expand=body.storage` to get page content. Use CQL search to find pages, then fetch by ID for full content.
"""

    def _setup_http_tools(self) -> None:
        endpoint = self._build_endpoint_config()
        http_config = HttpToolsetConfig(endpoints=[endpoint])
        http_toolset = HttpToolset(
            name="confluence",
            config=http_config,
            llm_instructions=self._build_llm_instructions(),
            enabled=True,
        )
        ok, msg = http_toolset.prerequisites_callable(http_config.model_dump())
        if not ok:
            raise RuntimeError(f"Failed to initialize HTTP toolset for Confluence: {msg}")

        self.tools = http_toolset.tools
        self.llm_instructions = http_toolset.llm_instructions
