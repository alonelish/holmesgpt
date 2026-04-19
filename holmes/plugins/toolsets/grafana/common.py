from typing import ClassVar, Dict, List, Optional

from pydantic import Field

from holmes.utils.pydantic_utils import ToolsetConfig

GRAFANA_ICON_URL = "https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/grafana.svg"
LOKI_ICON_URL = "https://grafana.com/media/docs/loki/logo-grafana-loki.png"


class GrafanaConfig(ToolsetConfig):
    """A config that represents one of the Grafana related tools like Loki or Tempo
    If `grafana_datasource_uid` is set, then it is assumed that Holmes will proxy all
    requests through grafana. In this case `api_url` should be the grafana URL.
    If `grafana_datasource_uid` is not set, it is assumed that the `api_url` is the
    systems' URL
    """

    _deprecated_mappings: ClassVar[Dict[str, Optional[str]]] = {
        "url": "api_url",
        "headers": "additional_headers",
    }

    api_url: str = Field(
        title="URL",
        description="Grafana URL or direct datasource URL",
        examples=["YOUR GRAFANA URL", "http://grafana.monitoring.svc:3000"],
    )
    api_key: Optional[str] = Field(
        default=None,
        title="API Key",
        description="Grafana API key for authentication",
        examples=["YOUR API KEY"],
    )
    additional_headers: Optional[Dict[str, str]] = Field(
        default=None,
        title="Additional Headers",
        description="Additional HTTP headers to include in requests",
        examples=[{"Authorization": "Bearer YOUR_API_KEY"}],
    )
    grafana_datasource_uid: Optional[str] = Field(
        default=None,
        title="Datasource UID",
        description="Grafana datasource UID to proxy requests through Grafana",
        examples=["loki", "tempo"],
    )
    external_url: Optional[str] = Field(
        default=None,
        title="External URL",
        description="External URL for linking to Grafana UI",
    )
    verify_ssl: bool = Field(
        default=True,
        title="Verify SSL",
        description="Whether to verify SSL certificates",
    )


def build_headers(api_key: Optional[str], additional_headers: Optional[Dict[str, str]]):
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if additional_headers:
        headers.update(additional_headers)

    return headers


def get_base_url(config: GrafanaConfig) -> str:
    if config.grafana_datasource_uid:
        return f"{config.api_url}/api/datasources/proxy/uid/{config.grafana_datasource_uid}"
    else:
        return config.api_url


class GrafanaLokiProxyConfig(GrafanaConfig):
    """Loki accessed via a Grafana datasource proxy (recommended)."""

    _name: ClassVar[Optional[str]] = "Loki via Grafana"
    _description: ClassVar[Optional[str]] = (
        "Query Loki through a Grafana datasource proxy. Recommended when you already "
        "have Grafana with a Loki datasource configured."
    )
    _icon_url: ClassVar[Optional[str]] = GRAFANA_ICON_URL
    _docs_anchor: ClassVar[Optional[str]] = "loki-via-grafana-recommended"
    _hidden_fields: ClassVar[List[str]] = ["additional_headers"]

    api_url: str = Field(  # type: ignore[assignment]
        title="Grafana URL",
        description="Base URL of your Grafana instance",
        examples=["https://grafana.example.com", "http://grafana.monitoring.svc:3000"],
    )
    api_key: str = Field(  # type: ignore[assignment]
        title="API Key",
        description="Grafana service account token with Viewer role",
        examples=["{{ env.GRAFANA_API_KEY }}"],
        json_schema_extra={"format": "password"},
    )
    grafana_datasource_uid: str = Field(  # type: ignore[assignment]
        title="Loki Datasource UID",
        description="UID of the Loki datasource configured in Grafana",
        examples=["loki"],
    )


class DirectLokiConfig(GrafanaConfig):
    """Direct connection to a self-hosted Loki API endpoint without Grafana."""

    _name: ClassVar[Optional[str]] = "Direct Loki"
    _description: ClassVar[Optional[str]] = (
        "Connect directly to a self-hosted Loki API endpoint without going through Grafana. "
        "Supports multi-tenancy via the X-Scope-OrgID header."
    )
    _icon_url: ClassVar[Optional[str]] = LOKI_ICON_URL
    _docs_anchor: ClassVar[Optional[str]] = "direct-loki"
    _hidden_fields: ClassVar[List[str]] = [
        "api_key",
        "grafana_datasource_uid",
        "external_url",
    ]

    api_url: str = Field(  # type: ignore[assignment]
        title="Loki URL",
        description="Base URL of your Loki server",
        examples=[
            "http://loki.monitoring.svc:3100",
            "http://loki-gateway.loki.svc.cluster.local",
        ],
    )
    additional_headers: Dict[str, str] = Field(
        default={"X-Scope-OrgID": "<tenant id>"},
        title="Additional Headers",
        description="Additional HTTP headers to include in requests",
    )


class GrafanaTempoLabelsConfig(ToolsetConfig):
    pod: str = Field(default="k8s.pod.name", title="Pod Label", description="Label for pod name")
    namespace: str = Field(default="k8s.namespace.name", title="Namespace Label", description="Label for namespace")
    deployment: str = Field(default="k8s.deployment.name", title="Deployment Label", description="Label for deployment")
    node: str = Field(default="k8s.node.name", title="Node Label", description="Label for node name")
    service: str = Field(default="service.name", title="Service Label", description="Label for service name")


class GrafanaTempoConfig(GrafanaConfig):
    labels: GrafanaTempoLabelsConfig = Field(
        default_factory=GrafanaTempoLabelsConfig,
        title="Labels",
        description="Label mappings for Tempo spans",
    )
