import pytest

from holmes.core.openai_formatting import (
    _normalize_json_schema_type,
    type_to_open_ai_schema,
)
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


class TestNormalizeJsonSchemaType:
    """Tests for _normalize_json_schema_type helper function."""

    def test_string_type_returns_not_nullable(self):
        primary, nullable = _normalize_json_schema_type("string")
        assert primary == "string"
        assert nullable is False

    def test_nullable_string_returns_nullable(self):
        primary, nullable = _normalize_json_schema_type(["string", "null"])
        assert primary == "string"
        assert nullable is True

    def test_null_first_extracts_primary(self):
        primary, nullable = _normalize_json_schema_type(["null", "integer"])
        assert primary == "integer"
        assert nullable is True

    def test_only_null_defaults_to_string(self):
        primary, nullable = _normalize_json_schema_type(["null"])
        assert primary == "string"
        assert nullable is True

    def test_multiple_types_takes_first_non_null(self):
        primary, nullable = _normalize_json_schema_type(["number", "string", "null"])
        assert primary == "number"
        assert nullable is True


class TestJsonSchemaTypeFormatting:
    """Tests for type_to_open_ai_schema with JSON Schema union types."""

    def test_nullable_string_non_strict_mode(self):
        """Nullable types should produce list type in output."""
        param = ToolParameter(type=["string", "null"], required=True)
        result = type_to_open_ai_schema(param, strict_mode=False)
        assert result == {"type": ["string", "null"]}

    def test_nullable_integer_non_strict_mode(self):
        """Nullable integer should produce list type."""
        param = ToolParameter(type=["integer", "null"], required=True)
        result = type_to_open_ai_schema(param, strict_mode=False)
        assert result == {"type": ["integer", "null"]}

    def test_nullable_number_non_strict_mode(self):
        """Nullable number should produce list type."""
        param = ToolParameter(type=["number", "null"], required=False)
        result = type_to_open_ai_schema(param, strict_mode=False)
        assert result == {"type": ["number", "null"]}

    def test_required_string_strict_mode_not_nullable(self):
        """Required string params in strict mode should NOT be nullable."""
        param = ToolParameter(type="string", required=True)
        result = type_to_open_ai_schema(param, strict_mode=True)
        assert result == {"type": "string"}

    def test_optional_string_strict_mode_becomes_nullable(self):
        """Optional params in strict mode should become nullable."""
        param = ToolParameter(type="string", required=False)
        result = type_to_open_ai_schema(param, strict_mode=True)
        assert result == {"type": ["string", "null"]}

    def test_explicitly_nullable_required_strict_mode(self):
        """Explicitly nullable types should remain nullable even if required."""
        param = ToolParameter(type=["string", "null"], required=True)
        result = type_to_open_ai_schema(param, strict_mode=True)
        assert result == {"type": ["string", "null"]}

    def test_null_only_type(self):
        """Type with only null should default to nullable string."""
        param = ToolParameter(type=["null"], required=False)
        result = type_to_open_ai_schema(param, strict_mode=False)
        assert result == {"type": ["string", "null"]}

    def test_multi_type_takes_first_non_null(self):
        """Multi-type should use first non-null type."""
        param = ToolParameter(type=["integer", "string", "null"], required=True)
        result = type_to_open_ai_schema(param, strict_mode=False)
        assert result == {"type": ["integer", "null"]}
