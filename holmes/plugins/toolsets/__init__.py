import importlib
import logging
import os
import os.path
from typing import Any, Callable, List, Optional, Union

import yaml  # type: ignore
from pydantic import ValidationError

import holmes.utils.env as env_utils
from holmes.common.env_vars import (
    DISABLE_PROMETHEUS_TOOLSET,
    USE_LEGACY_KUBERNETES_LOGS,
)
from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import Toolset, ToolsetType, ToolsetYamlFromConfig, YAMLToolset

THIS_DIR = os.path.abspath(os.path.dirname(__file__))


class _ToolsetRegistryEntry:
    """Registry entry for a Python toolset that supports lazy loading via importlib.

    Instead of importing all toolset modules at package load time, each entry
    records the module path and class name. The actual import and instantiation
    is deferred until load() is called. This avoids importing heavy dependencies
    (cloud SDKs, HTTP clients, etc.) for toolsets that won't be used.
    """

    __slots__ = ("module_path", "class_name", "name", "factory_kwargs_fn", "condition_fn")

    def __init__(
        self,
        module_path: str,
        class_name: str,
        name: str,
        factory_kwargs_fn: Optional[Callable[..., dict[str, Any]]] = None,
        condition_fn: Optional[Callable[[], bool]] = None,
    ):
        self.module_path = module_path
        self.class_name = class_name
        self.name = name
        self.factory_kwargs_fn = factory_kwargs_fn
        self.condition_fn = condition_fn

    def load(
        self,
        dal: Optional[SupabaseDal] = None,
        additional_search_paths: Optional[List[str]] = None,
    ) -> Optional[Toolset]:
        """Import the module and instantiate the toolset class."""
        if self.condition_fn and not self.condition_fn():
            return None
        try:
            module = importlib.import_module(self.module_path)
            cls = getattr(module, self.class_name)
            if self.factory_kwargs_fn:
                kwargs = self.factory_kwargs_fn(dal=dal, additional_search_paths=additional_search_paths)
                return cls(**kwargs)
            return cls()
        except Exception:
            logging.warning(
                f"Failed to load toolset {self.class_name} from {self.module_path}",
                exc_info=True,
            )
            return None


def _robusta_kwargs(dal: Optional[SupabaseDal] = None, **_: Any) -> dict[str, Any]:
    return {"dal": dal}


def _runbook_kwargs(
    dal: Optional[SupabaseDal] = None,
    additional_search_paths: Optional[List[str]] = None,
    **_: Any,
) -> dict[str, Any]:
    return {"dal": dal, "additional_search_paths": additional_search_paths}


def _not_disable_prometheus() -> bool:
    return not DISABLE_PROMETHEUS_TOOLSET


def _not_use_legacy_kubernetes_logs() -> bool:
    return not USE_LEGACY_KUBERNETES_LOGS


# Registry of Python toolsets with lazy loading support.
# Each entry defines the module path, class name, toolset name, and optional
# factory kwargs function / condition function. Modules are only imported when
# the entry's load() method is called.
_PYTHON_TOOLSET_REGISTRY: List[_ToolsetRegistryEntry] = [
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.investigator.core_investigation",
        "CoreInvestigationToolset",
        "core_investigation",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.internet.internet",
        "InternetToolset",
        "internet",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.connectivity_check",
        "ConnectivityCheckToolset",
        "connectivity_check",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.robusta.robusta",
        "RobustaToolset",
        "robusta",
        factory_kwargs_fn=_robusta_kwargs,
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.grafana.loki.toolset_grafana_loki",
        "GrafanaLokiToolset",
        "grafana/loki",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.grafana.toolset_grafana_tempo",
        "GrafanaTempoToolset",
        "grafana/tempo",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.newrelic.newrelic",
        "NewRelicToolset",
        "newrelic",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.grafana.toolset_grafana",
        "GrafanaToolset",
        "grafana/dashboards",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.internet.notion",
        "NotionToolset",
        "notion",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.kafka",
        "KafkaToolset",
        "kafka/admin",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.datadog.toolset_datadog_logs",
        "DatadogLogsToolset",
        "datadog/logs",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.datadog.toolset_datadog_general",
        "DatadogGeneralToolset",
        "datadog/general",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.datadog.toolset_datadog_metrics",
        "DatadogMetricsToolset",
        "datadog/metrics",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.datadog.toolset_datadog_traces",
        "DatadogTracesToolset",
        "datadog/traces",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.elasticsearch.opensearch_query_assist",
        "OpenSearchQueryAssistToolset",
        "opensearch/query_assist",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.coralogix.toolset_coralogix",
        "CoralogixToolset",
        "coralogix",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.rabbitmq.toolset_rabbitmq",
        "RabbitMQToolset",
        "rabbitmq/core",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.bash.bash_toolset",
        "BashExecutorToolset",
        "bash",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.kubectl_run.kubectl_run_toolset",
        "KubectlRunToolset",
        "kubectl-run",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.atlas_mongodb.mongodb_atlas",
        "MongoDBAtlasToolset",
        "MongoDBAtlas",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.runbook.runbook_fetcher",
        "RunbookToolset",
        "runbook",
        factory_kwargs_fn=_runbook_kwargs,
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.azure_sql.azure_sql_toolset",
        "AzureSQLToolset",
        "azure/sql",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.servicenow_tables.servicenow_tables",
        "ServiceNowTablesToolset",
        "servicenow/tables",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.elasticsearch.elasticsearch",
        "ElasticsearchDataToolset",
        "elasticsearch/data",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.elasticsearch.elasticsearch",
        "ElasticsearchClusterToolset",
        "elasticsearch/cluster",
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.prometheus.prometheus",
        "PrometheusToolset",
        "prometheus/metrics",
        condition_fn=_not_disable_prometheus,
    ),
    _ToolsetRegistryEntry(
        "holmes.plugins.toolsets.kubernetes_logs",
        "KubernetesLogsToolset",
        "kubernetes/logs",
        condition_fn=_not_use_legacy_kubernetes_logs,
    ),
]


def get_python_toolset_names() -> List[str]:
    """Return names of all registered Python toolsets without importing their modules."""
    return [
        entry.name
        for entry in _PYTHON_TOOLSET_REGISTRY
        if not entry.condition_fn or entry.condition_fn()
    ]


def load_toolsets_from_file(
    toolsets_path: str, strict_check: bool = True
) -> List[Toolset]:
    toolsets = []
    with open(toolsets_path) as file:
        parsed_yaml = yaml.safe_load(file)
        if parsed_yaml is None:
            raise ValueError(
                f"Failed to load toolsets from {toolsets_path}: file is empty or invalid YAML."
            )
        toolsets_dict = parsed_yaml.get("toolsets", {})
        mcp_config = parsed_yaml.get("mcp_servers", {})

        for server_config in mcp_config.values():
            server_config["type"] = ToolsetType.MCP.value
            server_config.setdefault("enabled", True)

        toolsets_dict.update(mcp_config)

        toolsets.extend(load_toolsets_from_config(toolsets_dict, strict_check))

    return toolsets


def load_python_toolsets(
    dal: Optional[SupabaseDal] = None,
    additional_search_paths: Optional[List[str]] = None,
    names_filter: Optional[set[str]] = None,
) -> List[Toolset]:
    """Load Python toolsets using lazy imports via the registry.

    Args:
        dal: Optional database access layer.
        additional_search_paths: Optional search paths for runbooks.
        names_filter: If provided, only load toolsets whose name is in this set.
                      If None, load all registered toolsets.
    """
    logging.debug("loading python toolsets")
    if names_filter is not None:
        logging.debug(f"Filtering python toolsets to: {names_filter}")

    toolsets: list[Toolset] = []
    for entry in _PYTHON_TOOLSET_REGISTRY:
        if names_filter is not None and entry.name not in names_filter:
            logging.debug(f"Skipping toolset {entry.name} (not in names filter)")
            continue
        ts = entry.load(dal=dal, additional_search_paths=additional_search_paths)
        if ts is not None:
            toolsets.append(ts)
    return toolsets


def load_builtin_toolsets(
    dal: Optional[SupabaseDal] = None,
    additional_search_paths: Optional[List[str]] = None,
    python_toolset_names_filter: Optional[set[str]] = None,
) -> List[Toolset]:
    """Load all built-in toolsets (YAML + Python).

    Args:
        dal: Optional database access layer.
        additional_search_paths: Optional search paths for runbooks.
        python_toolset_names_filter: If provided, only load Python toolsets whose name
                                     is in this set. YAML toolsets are always loaded
                                     (they are lightweight). If None, load all Python toolsets.
    """
    all_toolsets: List[Toolset] = []
    logging.debug(f"loading toolsets from {THIS_DIR}")

    # Handle YAML toolsets (always loaded - they are lightweight file parsing)
    for filename in os.listdir(THIS_DIR):
        if not filename.endswith(".yaml"):
            continue

        if filename == "kubernetes_logs.yaml" and not USE_LEGACY_KUBERNETES_LOGS:
            continue

        path = os.path.join(THIS_DIR, filename)
        toolsets_from_file = load_toolsets_from_file(path, strict_check=True)
        all_toolsets.extend(toolsets_from_file)

    all_toolsets.extend(
        load_python_toolsets(
            dal=dal,
            additional_search_paths=additional_search_paths,
            names_filter=python_toolset_names_filter,
        )
    )  # type: ignore

    # disable built-in toolsets by default, and the user can enable them explicitly in config.
    for toolset in all_toolsets:
        toolset.type = ToolsetType.BUILTIN
        # dont' expose build-in toolsets path
        toolset.path = None

    return all_toolsets  # type: ignore


def is_old_toolset_config(
    toolsets: Union[dict[str, dict[str, Any]], List[dict[str, Any]]],
) -> bool:
    # old config is a list of toolsets
    if isinstance(toolsets, list):
        return True
    return False


def load_toolsets_from_config(
    toolsets: dict[str, dict[str, Any]],
    strict_check: bool = True,
) -> List[Toolset]:
    """
    Load toolsets from a dictionary or list of dictionaries.
    :param toolsets: Dictionary of toolsets or list of toolset configurations.
    :param strict_check: If True, all required fields for a toolset must be present.
    :return: List of validated Toolset objects.
    """

    if not toolsets:
        return []

    loaded_toolsets: list[Toolset] = []
    if is_old_toolset_config(toolsets):
        message = "Old toolset config format detected, please update to the new format: https://holmesgpt.dev/data-sources/custom-toolsets/"
        logging.warning(message)
        raise ValueError(message)

    for name, config in toolsets.items():
        try:
            toolset_type = config.get("type", ToolsetType.BUILTIN.value)

            # Resolve env var placeholders before creating the Toolset.
            # If done after, .override_with() will overwrite resolved values with placeholders
            # because model_dump() returns the original, unprocessed config from YAML.
            if config:
                config = env_utils.replace_env_vars_values(config)

            validated_toolset: Optional[Toolset] = None
            # MCP server is not a built-in toolset, so we need to set the type explicitly
            if toolset_type == ToolsetType.MCP.value:
                from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset

                validated_toolset = RemoteMCPToolset(**config, name=name)
            elif toolset_type == ToolsetType.HTTP.value:
                from holmes.plugins.toolsets.http.http_toolset import HttpToolset

                validated_toolset = HttpToolset(name=name, **config)
            elif strict_check:
                validated_toolset = YAMLToolset(**config, name=name)  # type: ignore
            else:
                validated_toolset = ToolsetYamlFromConfig(  # type: ignore
                    **config, name=name
                )

            loaded_toolsets.append(validated_toolset)
        except ValidationError as e:
            logging.warning(f"Toolset '{name}' is invalid: {e}")

        except Exception:
            logging.warning("Failed to load toolset: %s", name, exc_info=True)

    return loaded_toolsets
