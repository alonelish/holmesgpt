import pytest

from holmes.core.openai_formatting import type_to_open_ai_schema
from holmes.core.tools import ToolParameter


@pytest.mark.parametrize(
    "toolset_type, open_ai_type",
    [
        (
            "int",
            {"type": "int"},
        ),
        (
            "string",
            {"type": "string"},
        ),
        (
            "array[int]",
            {"type": "array", "items": {"type": "int"}},
        ),
        (
            "array[string]",
            {"type": "array", "items": {"type": "string"}},
        ),
    ],
)
def test_type_to_open_ai_schema(toolset_type, open_ai_type):
    param = ToolParameter(type=toolset_type, required=True)
    result = type_to_open_ai_schema(param, strict_mode=False)
    assert result == open_ai_type


class TestJsonSchemaTypeFormatting:
    """Tests for type_to_open_ai_schema with JSON Schema union types (issue #1459)."""

    def test_nullable_type_passes_through(self):
        """Nullable types from MCP servers should produce list type in output."""
        param = ToolParameter(type=["string", "null"], required=True)
        result = type_to_open_ai_schema(param, strict_mode=False)
        assert result == {"type": ["string", "null"]}

    def test_explicitly_nullable_respected_in_strict_mode(self):
        """Explicitly nullable types should remain nullable even if required in strict mode."""
        param = ToolParameter(type=["string", "null"], required=True)
        result = type_to_open_ai_schema(param, strict_mode=True)
        assert result == {"type": ["string", "null"]}
