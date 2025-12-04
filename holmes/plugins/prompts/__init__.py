import os
import os.path
from typing import Optional
from jinja2 import Environment, FileSystemLoader
from datetime import datetime, timezone

THIS_DIR = os.path.abspath(os.path.dirname(__file__))

# Toolsets that enable link generation
LINK_ENABLED_TOOLSETS = {
    "coralogix/logs",
    "datadog/logs",
    "datadog/metrics",
    "datadog/traces",
    "datadog/general",
    "datadog/rds",
    "grafana/tempo",
    "grafana/loki",
    "newrelic",
    "grafana/dashboards",
}


def _check_links_enabled(toolsets) -> bool:
    """
    Check if links should be enabled based on enabled toolsets.

    Args:
        toolsets: List of Toolset objects

    Returns:
        True if any link-enabled toolset is enabled, False otherwise
    """
    if not toolsets:
        return False

    for toolset in toolsets:
        # Check if toolset has status attribute and it's enabled
        if hasattr(toolset, "status"):
            from holmes.core.tools import ToolsetStatusEnum

            if toolset.status == ToolsetStatusEnum.ENABLED:
                if hasattr(toolset, "name") and toolset.name in LINK_ENABLED_TOOLSETS:
                    return True
        # Fallback: check if toolset has enabled attribute
        elif hasattr(toolset, "enabled") and toolset.enabled:
            if hasattr(toolset, "name") and toolset.name in LINK_ENABLED_TOOLSETS:
                return True

    return False


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
    if "links_enabled" not in context:
        toolsets = context.get("toolsets", [])
        context["links_enabled"] = _check_links_enabled(toolsets)

    now = datetime.now(timezone.utc)
    context.update(
        {
            "now": f"{now}",
            "now_timestamp_seconds": int(now.timestamp()),
            "current_year": now.year,
        }
    )

    return template.render(**context)
