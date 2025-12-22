from typing import Any, Dict, Optional

from holmes.core.llm import LLM, TokenCountMetadata
from holmes.core.models import ToolCallResult
from holmes.core.task_subagent import TaskSubAgent, TaskSubAgentConfig
from holmes.core.tool_calling_llm import LLMResult
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Toolset,
    ToolsetStatusEnum,
)
from holmes.core.tools_utils.tool_executor import ToolExecutor


class SimpleLLM(LLM):
    def __init__(self):
        self.model = "mock-model"

    def get_context_window_size(self) -> int:
        return 8192

    def get_maximum_output_token(self) -> int:
        return 1024

    def count_tokens(
        self, messages: list[dict], tools: Optional[list[dict[str, Any]]] = None
    ) -> TokenCountMetadata:
        return TokenCountMetadata(
            total_tokens=100,
            tools_tokens=0,
            system_tokens=0,
            user_tokens=0,
            tools_to_call_tokens=0,
            assistant_tokens=0,
            other_tokens=0,
        )

    def completion(self, *args, **kwargs):  # type: ignore
        raise AssertionError("Main LLM completion should not be called in these tests")


class DummyToolset(Toolset):
    def get_example_config(self) -> Dict[str, Any]:
        return {}


def build_tool_executor() -> ToolExecutor:
    dummy_toolset = DummyToolset(
        enabled=True,
        name="dummy",
        description="dummy",
        tools=[],
        status=ToolsetStatusEnum.ENABLED,
    )
    return ToolExecutor(toolsets=[dummy_toolset])


def build_tool_call_result():
    result = StructuredToolResult(
        status=StructuredToolResultStatus.SUCCESS, data="data"
    )
    return ToolCallResult(
        tool_call_id="1",
        tool_name="test_tool",
        description="desc",
        result=result,
    )


class DummySubAgent:
    def __init__(self):
        self.calls = 0
        self.received_prompts: Dict[str, str] = {}

    def prompt_call(self, system_prompt: str, user_prompt: str, **kwargs):
        self.calls += 1
        self.received_prompts = {"system": system_prompt, "user": user_prompt}
        return LLMResult(result="subagent summary", tool_calls=[])


def test_returns_original_when_under_threshold():
    llm = SimpleLLM()
    tool_executor = build_tool_executor()
    dummy_subagent = DummySubAgent()
    subagent = TaskSubAgent(
        llm=llm,
        tool_executor=tool_executor,
        max_steps=4,
        config=TaskSubAgentConfig(summary_max_chars=500),
        subagent_factory=lambda *_args, **_kwargs: dummy_subagent,
    )
    tool_call_result = build_tool_call_result()
    message = tool_call_result.as_tool_call_message()
    message["content"] = "small content"

    updated_message, metadata = subagent.summarize_tool_message(
        tool_call_result, message
    )

    assert updated_message["content"] == message["content"]
    assert metadata is None
    assert dummy_subagent.calls == 0


def test_subagent_runs_when_over_threshold():
    llm = SimpleLLM()
    tool_executor = build_tool_executor()
    dummy_subagent = DummySubAgent()
    subagent = TaskSubAgent(
        llm=llm,
        tool_executor=tool_executor,
        max_steps=4,
        config=TaskSubAgentConfig(summary_max_chars=10, max_input_chars=50),
        subagent_factory=lambda *_args, **_kwargs: dummy_subagent,
    )
    tool_call_result = build_tool_call_result()
    long_content = "x" * 100
    message = tool_call_result.as_tool_call_message()
    message["content"] = long_content

    updated_message, metadata = subagent.summarize_tool_message(
        tool_call_result, message, parent_messages=[{"role": "user", "content": "ask"}]
    )

    assert "Task SubAgent Summary" in updated_message["content"]
    assert "subagent summary" in updated_message["content"]
    assert metadata is not None
    assert metadata["tool_call_id"] == "1"
    assert metadata["original_chars"] == len(long_content)
    assert dummy_subagent.calls == 1
