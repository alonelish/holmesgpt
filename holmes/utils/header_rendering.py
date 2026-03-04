"""Shared utility for rendering Jinja2 template headers with request context.

Used by MCP toolsets, HTTP toolsets, Python toolsets, and YAML toolsets
to propagate HTTP headers from incoming requests to outgoing API calls.
"""

import logging
import os
from typing import Any, Dict, Optional

from jinja2 import Template

logger = logging.getLogger(__name__)


class CaseInsensitiveDict(dict):
    """Dictionary with case-insensitive key lookup for HTTP headers."""

    def _find_key(self, key: str) -> Optional[str]:
        if isinstance(key, str):
            key_lower = key.lower()
            for k in dict.keys(self):
                if k.lower() == key_lower:
                    return k
        return None

    def __getitem__(self, key: str) -> Any:
        found = self._find_key(key)
        if found is not None:
            return dict.__getitem__(self, found)
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            return self._find_key(key) is not None
        return False

    def get(self, key: str, default: Any = None) -> Any:
        found = self._find_key(key)
        if found is not None:
            return dict.__getitem__(self, found)
        return default


def render_template_headers(
    extra_headers: Dict[str, str],
    request_context: Optional[Dict[str, Any]] = None,
    source_name: str = "unknown",
) -> Dict[str, str]:
    """Render a dictionary of Jinja2 template headers with request context and env vars.

    Args:
        extra_headers: Dict mapping header names to Jinja2 template strings.
            Templates can reference:
            - {{ request_context.headers['Header-Name'] }} for pass-through headers
            - {{ env.ENV_VAR_NAME }} for environment variables
            - Plain strings for static values
        request_context: Optional dict with structure {"headers": {"Name": "Value", ...}}.
            Passed through from the incoming HTTP request.
        source_name: Name of the toolset/component for logging purposes.

    Returns:
        Dict of rendered header name-value pairs. Headers that fail to render
        are skipped with a warning.
    """
    rendered = {}
    for header_name, header_template in extra_headers.items():
        try:
            rendered[header_name] = _render_single_template(
                header_template, request_context
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"'{source_name}': Failed to render header template "
                f"'{header_name}': {e}"
            )
    return rendered


def _render_single_template(
    template_str: str,
    request_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Render a single Jinja2 template string.

    Supports:
    - {{ request_context.headers['Header-Name'] }} - case-insensitive header lookup
    - {{ env.ENV_VAR }} - environment variables
    - Plain strings (no template syntax) - returned as-is

    Raises on failure so the caller can decide whether to skip or propagate.
    """
    context: Dict[str, Any] = {
        "env": os.environ,
    }

    if request_context:
        request_context_copy = request_context.copy()
        if "headers" in request_context_copy:
            request_context_copy["headers"] = CaseInsensitiveDict(
                request_context_copy["headers"]
            )
        context["request_context"] = request_context_copy
    else:
        context["request_context"] = {"headers": CaseInsensitiveDict()}

    template = Template(template_str)
    return template.render(context)
