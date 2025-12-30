import json
import logging
from typing import Any, Dict, Optional, Tuple

import jq  # type: ignore
from jsonpath_ng import parse as jsonpath_parse  # type: ignore

from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus, ToolParameter

logger = logging.getLogger(__name__)


def _truncate_to_depth(value: Any, max_depth: Optional[int], current_depth: int = 0):
    """Recursively truncate dictionaries/lists beyond the requested depth."""
    if max_depth is None or max_depth < 0:
        return value

    if current_depth >= max_depth:
        if isinstance(value, (dict, list)):
            return f"...truncated at depth {max_depth}"
        return value

    if isinstance(value, dict):
        return {
            key: _truncate_to_depth(sub_value, max_depth, current_depth + 1)
            for key, sub_value in value.items()
        }
    if isinstance(value, list):
        return [
            _truncate_to_depth(item, max_depth, current_depth + 1) for item in value
        ]

    return value


def _apply_jsonpath_filter(data: Any, expression: str) -> Tuple[Optional[Any], Optional[str]]:
    try:
        jsonpath_expr = jsonpath_parse(expression)
        matches = [match.value for match in jsonpath_expr.find(data)]
        if len(matches) == 1:
            return matches[0], None
        return matches, None
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to apply jsonpath filter", exc_info=exc)
        return None, f"Invalid jsonpath expression: {exc}"


def _apply_jq_filter(data: Any, expression: str) -> Tuple[Optional[Any], Optional[str]]:
    try:
        compiled = jq.compile(expression)
        results = compiled.input(data).all()
        if len(results) == 1:
            return results[0], None
        return results, None
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to apply jq filter", exc_info=exc)
        return None, f"Invalid jq filter: {exc}"


class JsonFilterMixin:
    """Opt-in mixin for tools that return JSON and want filtering controls."""

    filter_parameters: Dict[str, ToolParameter] = {
        "max_depth": ToolParameter(
            description="Maximum nesting depth to return from the JSON (0 returns only top-level keys). Leave empty for full response.",
            type="integer",
            required=False,
        ),
        "jsonpath": ToolParameter(
            description="Optional jsonpath expression to extract specific parts of the JSON.",
            type="string",
            required=False,
        ),
        "jq": ToolParameter(
            description="Optional jq filter to apply to the JSON.",
            type="string",
            required=False,
        ),
    }

    @classmethod
    def extend_parameters(cls, existing: Dict[str, ToolParameter]) -> Dict[str, ToolParameter]:
        merged = dict(cls.filter_parameters)
        merged.update(existing)
        return merged

    def _filter_result_data(self, data: Any, params: Dict) -> Tuple[Any, Optional[str]]:
        parsed_data = data
        if isinstance(data, str):
            try:
                parsed_data = json.loads(data)
            except Exception:
                # Not JSON, leave as-is
                return data, None

        if params.get("jsonpath"):
            parsed_data, error = _apply_jsonpath_filter(parsed_data, params["jsonpath"])
            if error:
                return None, error

        if params.get("jq"):
            parsed_data, error = _apply_jq_filter(parsed_data, params["jq"])
            if error:
                return None, error

        parsed_data = _truncate_to_depth(parsed_data, params.get("max_depth"))
        return parsed_data, None

    def filter_result(self, result: StructuredToolResult, params: Dict) -> StructuredToolResult:
        filtered_data, error = self._filter_result_data(result.data, params)
        if error:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error,
                params=params,
                url=result.url,
                invocation=result.invocation,
                icon_url=result.icon_url,
            )

        result.data = filtered_data
        return result
