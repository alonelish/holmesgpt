from holmes.core.tools import ToolsetStatusEnum, ToolsetTag
from holmes.plugins.toolsets.investigator.core_investigation import (
    CoreInvestigationToolset,
    JqQueryTool,
    TodoWriteTool,
)


class TestCoreInvestigationToolset:
    def test_toolset_creation(self):
        """Test that CoreInvestigationToolset is created correctly."""
        toolset = CoreInvestigationToolset()

        assert toolset.name == "core_investigation"
        assert "investigation tools" in toolset.description
        assert toolset.enabled is True
        assert toolset.is_default is True
        assert ToolsetTag.CORE in toolset.tags

    def test_toolset_has_todo_write_tool(self):
        """Test that the toolset includes the TodoWrite and JqQuery tools."""
        toolset = CoreInvestigationToolset()

        assert len(toolset.tools) == 2
        assert isinstance(toolset.tools[0], TodoWriteTool)
        assert toolset.tools[0].name == "TodoWrite"
        assert isinstance(toolset.tools[1], JqQueryTool)
        assert toolset.tools[1].name == "jq_query"

    def test_toolset_check_prerequisites(self):
        """Test that toolset prerequisites check passes."""
        toolset = CoreInvestigationToolset()
        toolset.check_prerequisites()

        # Should be enabled by default with no prerequisites
        assert toolset.status == ToolsetStatusEnum.ENABLED
        assert toolset.error is None


class TestJqQueryTool:
    def _make_context(self):
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.tool_number = 1
        ctx.tool_call_id = "test"
        ctx.tool_name = "jq_query"
        return ctx

    def test_count_items(self):
        tool = JqQueryTool()
        result = tool._invoke(
            {"data": '[{"a":1},{"a":2},{"a":3}]', "expression": ". | length"},
            self._make_context(),
        )
        assert result.data == 3

    def test_group_and_count(self):
        tool = JqQueryTool()
        data = '[{"sev":"high"},{"sev":"low"},{"sev":"high"},{"sev":"low"},{"sev":"low"}]'
        result = tool._invoke(
            {"data": data, "expression": "group_by(.sev) | map({severity: .[0].sev, count: length})"},
            self._make_context(),
        )
        assert result.data == [{"severity": "high", "count": 2}, {"severity": "low", "count": 3}]

    def test_filter_and_count(self):
        tool = JqQueryTool()
        data = '[{"status":"open"},{"status":"closed"},{"status":"open"}]'
        result = tool._invoke(
            {"data": data, "expression": "[.[] | select(.status == \"open\")] | length"},
            self._make_context(),
        )
        assert result.data == 2

    def test_invalid_json(self):
        tool = JqQueryTool()
        result = tool._invoke(
            {"data": "not json", "expression": ". | length"},
            self._make_context(),
        )
        assert result.status.value == "error"
        assert "Invalid JSON" in result.error

    def test_invalid_expression(self):
        tool = JqQueryTool()
        result = tool._invoke(
            {"data": "[1,2,3]", "expression": ".[[invalid"},
            self._make_context(),
        )
        assert result.status.value == "error"
        assert "jq expression error" in result.error
