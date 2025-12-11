import os
import os.path
from typing import Optional
from jinja2 import Environment, FileSystemLoader
from datetime import datetime, timezone
import logging

THIS_DIR = os.path.abspath(os.path.dirname(__file__))

# Toolsets that enable link generation
LINK_ENABLED_TOOLSETS = {
    "grafana/tempo",
    "grafana/loki",
    "grafana/dashboards",

    ## Not yet supported toolsets
    # "coralogix/logs",
    # "datadog/logs",
    # "datadog/metrics",
    # "datadog/traces",
    # "datadog/general",
    # "datadog/rds",
    # "newrelic",
}


def _is_toolset_enabled(toolset) -> bool:
    if hasattr(toolset, "status"):
        # ToolsetStatusEnum.ENABLED is "enabled" (string enum)
        return toolset.status == "enabled"
    elif hasattr(toolset, "enabled"):
        return toolset.enabled
    return False


def _is_link_enabled_toolset(toolset) -> bool:
    if not _is_toolset_enabled(toolset):
        return False
    return hasattr(toolset, "name") and toolset.name in LINK_ENABLED_TOOLSETS


def _check_links_enabled(toolsets) -> bool:
    if not toolsets:
        return False

    for toolset in toolsets:
        if _is_link_enabled_toolset(toolset):
            return True

    return False


def _extract_toolset_base_urls(toolsets) -> dict[str, str]:
    if not toolsets:
        return {}

    url_map = {}
    for toolset in toolsets:
        if not _is_link_enabled_toolset(toolset):
            continue

        # Extract base name (e.g., "grafana/tempo" -> "grafana")
        toolset_name = toolset.name
        base_name = toolset_name.split("/")[0] if "/" in toolset_name else toolset_name

        logging.error(f"toolset: {toolset}")
        # Extract URL from Grafana toolsets
        if hasattr(toolset, "_grafana_config"):
            config = toolset._grafana_config
            base_url = getattr(config, "external_url", None) or getattr(config, "url", None)
            if base_url:
                url_map[base_name] = str(base_url).rstrip("/")

    return url_map


def load_prompt(prompt: str) -> str:
    """
    prompt is either in the format 'builtin://' or 'file://' or a regular string
    builtins are loaded as a file from this directory
    files are loaded from the file system normally
    regular strings are returned as is (as literal strings)
    """
    if prompt.startswith("builtin://"):
        path = os.path.join(THIS_DIR, prompt[len("builtin://") :])
    elif prompt.startswith("file://"):
        path = prompt[len("file://") :]
    else:
        return prompt

    return open(path, encoding="utf-8").read()


def load_and_render_prompt(prompt: str, context: Optional[dict] = None) -> str:
    """
    prompt is in the format 'builtin://' or 'file://' or a regular string
    see load_prompt() for details

    context is a dictionary of variables to be passed to the jinja2 template
    """
    prompt_as_str = load_prompt(prompt)

    env = Environment(
        loader=FileSystemLoader(THIS_DIR),
    )

    template = env.from_string(prompt_as_str)

    if context is None:
        context = {}

    # Check if links_enabled should be set based on toolsets
    toolsets = context.get("toolsets", [])
    if "links_enabled" not in context:
        context["links_enabled"] = _check_links_enabled(toolsets)

    # Extract base URLs from link-enabled toolsets
    if "links_enabled" in context and "toolset_base_urls" not in context:
        context["toolset_base_urls"] = _extract_toolset_base_urls(toolsets)

    now = datetime.now(timezone.utc)
    context.update(
        {
            "now": f"{now}",
            "now_timestamp_seconds": int(now.timestamp()),
            "current_year": now.year,
        }
    )

    return template.render(**context)
