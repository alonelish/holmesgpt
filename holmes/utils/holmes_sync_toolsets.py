import json
import logging
import os
import yaml
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, List, Optional

from holmes.config import Config
from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import PrerequisiteCacheMode, Toolset, ToolsetDBModel, ToolsetTag
from holmes.plugins.prompts import load_and_render_prompt
from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset

# Default in-pod path mounted by Kubernetes for every Pod with a service
# account token (the default). Used as the fallback when POD_NAMESPACE isn't
# wired up via the downward API.
_SERVICEACCOUNT_NAMESPACE_FILE = Path(
    "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
)


@lru_cache(maxsize=1)
def _detect_runner_namespace() -> Optional[str]:
    """Best-effort detection of the namespace the Holmes runner Pod is in.

    Order of preference:
        1. ``POD_NAMESPACE`` env var (set via the Kubernetes downward API:
           ``valueFrom.fieldRef.fieldPath: metadata.namespace``). Preferred
           because it's explicit and easy to override in tests.
        2. The service-account namespace file mounted by default into every
           Pod that has a service account token, ``/var/run/secrets/...``.

    Returns ``None`` when neither is available — i.e. when Holmes runs
    outside Kubernetes (CLI / local dev). Callers should omit the key
    rather than fabricate a value in that case.

    Cached because the namespace is invariant for the lifetime of the
    process, and the sync function may run repeatedly.
    """
    env_val = os.environ.get("POD_NAMESPACE")
    if env_val and env_val.strip():
        return env_val.strip()
    try:
        if _SERVICEACCOUNT_NAMESPACE_FILE.is_file():
            content = _SERVICEACCOUNT_NAMESPACE_FILE.read_text(encoding="utf-8").strip()
            if content:
                return content
    except OSError as exc:
        logging.debug(f"Failed to read service-account namespace file: {exc}")
    return None


def log_toolsets_statuses(toolsets: List[Toolset]):
    enabled_toolsets = [
        toolset.name for toolset in toolsets if toolset.status.value == "enabled"
    ]
    disabled_toolsets = [
        toolset.name for toolset in toolsets if toolset.status.value != "enabled"
    ]
    logging.info(f"Enabled toolsets: {enabled_toolsets}")
    logging.info(f"Disabled toolsets: {disabled_toolsets}")


def holmes_sync_toolsets_status(dal: SupabaseDal, config: Config) -> None:
    """
    Method for synchronizing toolsets with the database:
    1) Fetch all built-in toolsets from the holmes/plugins/toolsets directory
    2) Load custom toolsets defined in /etc/holmes/config/custom_toolset.yaml
    3) Override default toolsets with corresponding custom configurations
       and add any new custom toolsets that are not part of the defaults
    4) Run the check_prerequisites method for each toolset
    5) Use sync_toolsets to upsert toolset's status and remove toolsets that are not loaded from configs or folder with default directory
    """
    tool_executor = config.create_tool_executor(
        dal,
        toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLUSTER],
        enable_all_toolsets_possible=False,
        prerequisite_cache=PrerequisiteCacheMode.DISABLED,
        reuse_executor=True,
    )

    if not config.cluster_name:
        raise Exception(
            "Cluster name is missing in the configuration. Please ensure 'CLUSTER_NAME' is defined in the environment variables, "
            "or verify that a cluster name is provided in the Robusta configuration file."
        )

    db_toolsets = []
    updated_at = datetime.now().isoformat()
    runner_namespace = _detect_runner_namespace()
    for toolset in tool_executor.toolsets:
        # hiding disabled experimental toolsets from the docs
        if toolset.experimental and not toolset.enabled:
            continue

        if not toolset.installation_instructions:
            instructions = get_config_schema_for_toolset(toolset)
            toolset.installation_instructions = instructions
        # Use toolset's own meta if set (e.g., database with subtype),
        # otherwise fall back to writing the toolset type if available.
        meta = toolset.meta
        if meta is None and toolset.type:
            meta = {"type": toolset.type.value}
        if isinstance(toolset, RemoteMCPToolset):
            oauth_config = toolset.get_oauth_config()
            if oauth_config:
                meta = meta or {}
                meta["oauth_config"] = oauth_config

        # Tag every synced row with the runner's own namespace so downstream
        # consumers (frontend / debugging tooling) can tell where the
        # status row came from. Skipped when running outside Kubernetes.
        if runner_namespace:
            meta = meta or {}
            meta["namespace"] = runner_namespace

        db_toolsets.append(
            ToolsetDBModel(
                toolset_name=toolset.name,
                cluster_id=config.cluster_name,
                account_id=dal.account_id,
                updated_at=updated_at,
                icon_url=toolset.icon_url,
                status=toolset.status.value if toolset.status else None,
                error=toolset.error,
                description=toolset.description,
                docs_url=toolset.docs_url,
                installation_instructions=toolset.installation_instructions,
                meta=meta,
            ).model_dump()
        )
    dal.sync_toolsets(db_toolsets, config.cluster_name)
    log_toolsets_statuses(tool_executor.toolsets)


def get_config_schema_for_toolset(toolset: Toolset) -> str:
    res: dict = {
        "example_yaml": render_default_installation_instructions_for_toolset(toolset),
        "schema": toolset.get_config_schema(),
    }
    return json.dumps(res)

def render_default_installation_instructions_for_toolset(toolset: Toolset) -> str:
    env_vars = toolset.get_environment_variables()
    context: dict[str, Any] = {
        "env_vars": env_vars if env_vars else [],
        "toolset_name": toolset.name,
    }

    example_config = toolset.get_config_example()
    if example_config:
        context["example_config"] = yaml.dump(example_config)

    # Emit top-level `subtype:` in the example YAML for multi-variant toolsets
    # (e.g. Prometheus, Database) so users who copy the example verbatim land
    # on the correct variant. The ToolsetConfig subclass declares `_subtype`.
    if toolset.config_classes:
        subtype = getattr(toolset.config_classes[0], "_subtype", None)
        if subtype:
            context["subtype"] = subtype

    installation_instructions = load_and_render_prompt(
        "file://holmes/utils/default_toolset_installation_guide.jinja2", context
    )
    return installation_instructions
