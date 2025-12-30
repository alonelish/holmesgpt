from types import SimpleNamespace
from unittest.mock import MagicMock

from holmes.core.tool_calling_llm import ToolCallingLLM


def test_anthropic_code_mode_adds_code_execution_tool():
    tool_executor = MagicMock()
    llm = SimpleNamespace(model="anthropic/claude-3-5-sonnet-20241022")
    toolcalling_llm = ToolCallingLLM(
        tool_executor=tool_executor,
        max_steps=1,
        llm=llm,
        anthropic_code_mode=True,
    )

    tools = [{"type": "function", "function": {"name": "do_something"}}]
    updated_tools = toolcalling_llm._maybe_enable_anthropic_code_mode(list(tools))

    assert any(tool.get("name") == "code_execution" for tool in updated_tools)
    assert (
        sum(1 for tool in updated_tools if tool.get("name") == "code_execution") == 1
    )


def test_anthropic_code_mode_skipped_for_non_anthropic_models():
    tool_executor = MagicMock()
    llm = SimpleNamespace(model="gpt-4.1")
    toolcalling_llm = ToolCallingLLM(
        tool_executor=tool_executor,
        max_steps=1,
        llm=llm,
        anthropic_code_mode=True,
    )

    updated_tools = toolcalling_llm._maybe_enable_anthropic_code_mode([])

    assert all(tool.get("name") != "code_execution" for tool in updated_tools)
