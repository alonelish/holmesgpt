"""Tests for the unified _run_loop() generator and AgentEvent types."""

import json
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from holmes.core.agent_events import (
    ApprovalRequiredEvent,
    CompletionEvent,
    IterationStartEvent,
    LLMResponseEvent,
    TokenCountEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from holmes.core.llm import LLM, TokenCountMetadata
from holmes.core.models import StructuredToolResult, StructuredToolResultStatus
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools_utils.tool_executor import ToolExecutor


def _make_token_count_metadata() -> TokenCountMetadata:
    return TokenCountMetadata(
        total_tokens=100,
        system_tokens=0,
        tools_to_call_tokens=0,
        tools_tokens=0,
        user_tokens=0,
        assistant_tokens=0,
        other_tokens=0,
    )


def _create_mock_llm_response(content="Hello", tool_calls=None):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message = MagicMock()
    mock_response.choices[0].message.content = content
    mock_response.choices[0].message.tool_calls = tool_calls
    mock_response.choices[0].message.reasoning_content = None
    mock_response.choices[0].message.model_dump.return_value = {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in (tool_calls or [])
        ]
        if tool_calls
        else None,
    }
    mock_response.to_json.return_value = json.dumps(
        {"choices": [{"message": {"content": content}}]}
    )
    return mock_response


def _create_mock_tool_call(
    tool_call_id="tool_1", tool_name="my_tool", arguments=None
):
    mock_tool_call = MagicMock()
    mock_tool_call.id = tool_call_id
    mock_tool_call.function = MagicMock()
    mock_tool_call.function.name = tool_name
    mock_tool_call.function.arguments = json.dumps(arguments or {"key": "value"})
    return mock_tool_call


def _create_ai(mock_llm, mock_tool_executor, max_steps=5) -> ToolCallingLLM:
    return ToolCallingLLM(
        tool_executor=mock_tool_executor,
        max_steps=max_steps,
        llm=mock_llm,
        tool_results_dir=None,
    )


def _setup_llm_mock(mock_llm):
    mock_llm.count_tokens.return_value = _make_token_count_metadata()
    mock_llm.get_context_window_size.return_value = 128000
    mock_llm.get_maximum_output_token.return_value = 4096
    mock_llm.get_max_token_count_for_single_tool.return_value = 50000
    mock_llm.model = "gpt-4o"


def _setup_tool_executor_mock(mock_tool_executor, tool_names=None):
    if tool_names is None:
        tool_names = ["my_tool"]
    tools = []
    for name in tool_names:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"A test tool called {name}",
                    "parameters": {
                        "type": "object",
                        "properties": {"key": {"type": "string"}},
                    },
                },
            }
        )
    mock_tool_executor.get_all_tools_openai_format.return_value = tools
    mock_tool_executor.enabled_toolsets = []


class TestRunLoopSimpleQA:
    """Test _run_loop with no tool calls (simple Q&A)."""

    def test_yields_correct_event_sequence(self):
        mock_llm = MagicMock(spec=LLM)
        mock_tool_executor = MagicMock(spec=ToolExecutor)
        _setup_llm_mock(mock_llm)
        _setup_tool_executor_mock(mock_tool_executor)

        # LLM returns a text-only response (no tool calls)
        mock_llm.completion.return_value = _create_mock_llm_response(
            content="The answer is 42"
        )

        ai = _create_ai(mock_llm, mock_tool_executor)
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "What is the meaning of life?"},
        ]

        events = list(ai._run_loop(messages=messages))
        event_types = [type(e) for e in events]

        assert IterationStartEvent in event_types
        assert TokenCountEvent in event_types
        assert LLMResponseEvent in event_types
        assert CompletionEvent in event_types

        # Check completion event
        completion = [e for e in events if isinstance(e, CompletionEvent)][0]
        assert completion.result == "The answer is 42"
        assert completion.num_llm_calls == 1

        # LLMResponseEvent should have has_tool_calls=False
        llm_response = [e for e in events if isinstance(e, LLMResponseEvent)][0]
        assert llm_response.has_tool_calls is False
        assert llm_response.content == "The answer is 42"


class TestRunLoopWithToolCalls:
    """Test _run_loop with tool calls."""

    def test_yields_tool_start_and_result_events(self):
        mock_llm = MagicMock(spec=LLM)
        mock_tool_executor = MagicMock(spec=ToolExecutor)
        _setup_llm_mock(mock_llm)
        _setup_tool_executor_mock(mock_tool_executor)

        # First call: LLM returns a tool call
        mock_tool_call = _create_mock_tool_call()
        response_with_tools = _create_mock_llm_response(
            content="Let me check", tool_calls=[mock_tool_call]
        )
        # Second call: LLM returns final answer
        final_response = _create_mock_llm_response(content="The result is X")
        mock_llm.completion.side_effect = [response_with_tools, final_response]

        # Mock tool execution
        mock_tool = MagicMock()
        mock_tool.get_parameterized_one_liner.return_value = "my_tool(key=value)"
        mock_tool.invoke.return_value = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data="tool output",
            params={"key": "value"},
        )
        mock_tool.requires_approval.return_value = None
        mock_tool_executor.get_tool_by_name.return_value = mock_tool
        mock_tool_executor.ensure_toolset_initialized.return_value = None

        ai = _create_ai(mock_llm, mock_tool_executor)
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Run the tool"},
        ]

        events = list(ai._run_loop(messages=messages))
        event_types = [type(e) for e in events]

        # Should have tool-related events
        assert ToolStartEvent in event_types
        assert ToolResultEvent in event_types

        # Check tool start event
        tool_start = [e for e in events if isinstance(e, ToolStartEvent)][0]
        assert tool_start.tool_name == "my_tool"
        assert tool_start.tool_call_id == "tool_1"

        # Check tool result event
        tool_result = [e for e in events if isinstance(e, ToolResultEvent)][0]
        assert tool_result.tool_call_result.tool_name == "my_tool"
        assert (
            tool_result.tool_call_result.result.status
            == StructuredToolResultStatus.SUCCESS
        )

        # Should end with completion
        completion = [e for e in events if isinstance(e, CompletionEvent)][0]
        assert completion.result == "The result is X"
        assert completion.num_llm_calls == 2

        # tool_calls in CompletionEvent should contain the tool call result
        assert len(completion.tool_calls) == 1


class TestRunLoopApprovalRequired:
    """Test _run_loop with approval required tools."""

    def test_streaming_mode_yields_approval_event(self):
        mock_llm = MagicMock(spec=LLM)
        mock_tool_executor = MagicMock(spec=ToolExecutor)
        _setup_llm_mock(mock_llm)
        _setup_tool_executor_mock(mock_tool_executor, tool_names=["dangerous_tool"])

        mock_tool_call = _create_mock_tool_call(
            tool_name="dangerous_tool", tool_call_id="tc_1"
        )
        response_with_tools = _create_mock_llm_response(
            content="Running dangerous command", tool_calls=[mock_tool_call]
        )
        mock_llm.completion.return_value = response_with_tools

        # Mock tool to return APPROVAL_REQUIRED
        def mock_invoke(
            tool_name, tool_params, user_approved, tool_call_id,
            tool_number=None, session_approved_prefixes=None,
            request_context=None,
        ):
            return StructuredToolResult(
                status=StructuredToolResultStatus.APPROVAL_REQUIRED,
                data="Requires approval",
                params=tool_params,
            )

        mock_tool = MagicMock()
        mock_tool.get_parameterized_one_liner.return_value = "dangerous_tool(key=value)"
        mock_tool_executor.get_tool_by_name.return_value = mock_tool
        mock_tool_executor.ensure_toolset_initialized.return_value = None

        ai = _create_ai(mock_llm, mock_tool_executor)
        ai._directly_invoke_tool_call = mock_invoke

        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Run dangerous command"},
        ]

        events = list(
            ai._run_loop(messages=messages, enable_tool_approval=True)
        )
        event_types = [type(e) for e in events]

        # Should yield ApprovalRequiredEvent and stop
        assert ApprovalRequiredEvent in event_types
        # Should NOT have CompletionEvent (loop paused)
        assert CompletionEvent not in event_types

        approval_event = [
            e for e in events if isinstance(e, ApprovalRequiredEvent)
        ][0]
        assert len(approval_event.pending_approvals) == 1
        assert approval_event.pending_approvals[0].tool_name == "dangerous_tool"

    def test_non_streaming_mode_handles_approval_via_callback(self):
        mock_llm = MagicMock(spec=LLM)
        mock_tool_executor = MagicMock(spec=ToolExecutor)
        _setup_llm_mock(mock_llm)
        _setup_tool_executor_mock(mock_tool_executor, tool_names=["dangerous_tool"])

        mock_tool_call = _create_mock_tool_call(
            tool_name="dangerous_tool", tool_call_id="tc_1"
        )
        response_with_tools = _create_mock_llm_response(
            content="Running command", tool_calls=[mock_tool_call]
        )
        final_response = _create_mock_llm_response(content="Done!")
        mock_llm.completion.side_effect = [response_with_tools, final_response]

        # Mock tool execution: first returns APPROVAL_REQUIRED, then SUCCESS on re-invoke
        call_count = 0

        def mock_invoke(
            tool_name, tool_params, user_approved, tool_call_id,
            tool_number=None, session_approved_prefixes=None,
            request_context=None,
        ):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.APPROVAL_REQUIRED,
                    data="Requires approval",
                    params=tool_params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data="command output",
                params=tool_params,
            )

        mock_tool = MagicMock()
        mock_tool.get_parameterized_one_liner.return_value = "dangerous_tool(key=value)"
        mock_tool.requires_approval.return_value = None
        mock_tool_executor.get_tool_by_name.return_value = mock_tool
        mock_tool_executor.ensure_toolset_initialized.return_value = None

        ai = _create_ai(mock_llm, mock_tool_executor)
        ai._directly_invoke_tool_call = mock_invoke
        # Set approval callback to always approve
        ai.approval_callback = lambda result: (True, None)

        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Run command"},
        ]

        events = list(
            ai._run_loop(messages=messages, enable_tool_approval=False)
        )
        event_types = [type(e) for e in events]

        # Should NOT yield ApprovalRequiredEvent
        assert ApprovalRequiredEvent not in event_types
        # Should complete normally
        assert CompletionEvent in event_types

        completion = [e for e in events if isinstance(e, CompletionEvent)][0]
        assert completion.result == "Done!"


class TestCallAdapter:
    """Test that call() correctly consumes _run_loop() events."""

    def test_call_returns_llm_result(self):
        mock_llm = MagicMock(spec=LLM)
        mock_tool_executor = MagicMock(spec=ToolExecutor)
        _setup_llm_mock(mock_llm)
        _setup_tool_executor_mock(mock_tool_executor)

        mock_llm.completion.return_value = _create_mock_llm_response(
            content="Simple answer"
        )

        ai = _create_ai(mock_llm, mock_tool_executor)
        result = ai.call(
            messages=[
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
            ]
        )

        assert result.result == "Simple answer"
        assert result.num_llm_calls == 1
        assert result.messages is not None


class TestCallStreamAdapter:
    """Test that call_stream() correctly maps _run_loop() events to StreamMessages."""

    def test_call_stream_yields_stream_messages(self):
        from holmes.utils.stream import StreamEvents

        mock_llm = MagicMock(spec=LLM)
        mock_tool_executor = MagicMock(spec=ToolExecutor)
        _setup_llm_mock(mock_llm)
        _setup_tool_executor_mock(mock_tool_executor)

        mock_llm.completion.return_value = _create_mock_llm_response(
            content="Streamed answer"
        )

        ai = _create_ai(mock_llm, mock_tool_executor)
        stream_messages = list(
            ai.call_stream(
                system_prompt="You are helpful",
                user_prompt="Hello",
            )
        )

        # Should have TOKEN_COUNT and ANSWER_END events
        event_types = [m.event for m in stream_messages]
        assert StreamEvents.TOKEN_COUNT in event_types
        assert StreamEvents.ANSWER_END in event_types

        # Check ANSWER_END content
        answer_end = [
            m for m in stream_messages if m.event == StreamEvents.ANSWER_END
        ][0]
        assert answer_end.data["content"] == "Streamed answer"

    def test_call_stream_with_tool_calls(self):
        from holmes.utils.stream import StreamEvents

        mock_llm = MagicMock(spec=LLM)
        mock_tool_executor = MagicMock(spec=ToolExecutor)
        _setup_llm_mock(mock_llm)
        _setup_tool_executor_mock(mock_tool_executor)

        mock_tool_call = _create_mock_tool_call()
        response_with_tools = _create_mock_llm_response(
            content="Let me check", tool_calls=[mock_tool_call]
        )
        final_response = _create_mock_llm_response(content="Result")
        mock_llm.completion.side_effect = [response_with_tools, final_response]

        mock_tool = MagicMock()
        mock_tool.get_parameterized_one_liner.return_value = "my_tool(key=value)"
        mock_tool.invoke.return_value = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data="output",
            params={"key": "value"},
        )
        mock_tool.requires_approval.return_value = None
        mock_tool_executor.get_tool_by_name.return_value = mock_tool
        mock_tool_executor.ensure_toolset_initialized.return_value = None

        ai = _create_ai(mock_llm, mock_tool_executor)
        stream_messages = list(
            ai.call_stream(
                system_prompt="You are helpful",
                user_prompt="Run tool",
            )
        )

        event_types = [m.event for m in stream_messages]
        assert StreamEvents.START_TOOL in event_types
        assert StreamEvents.TOOL_RESULT in event_types
        assert StreamEvents.ANSWER_END in event_types
