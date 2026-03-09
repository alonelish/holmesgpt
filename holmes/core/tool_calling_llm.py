import concurrent.futures
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Type, Union

import sentry_sdk
from openai import BadRequestError
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
)
from pydantic import BaseModel, Field
from rich.console import Console

from holmes.core.agent_events import (
    AgentEvent,
    ApprovalRequiredEvent,
    CompactionEvent,
    CompletionEvent,
    IterationStartEvent,
    LLMResponseEvent,
    TokenCountEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from holmes.common.env_vars import (
    LOG_LLM_USAGE_RESPONSE,
    RESET_REPEATED_TOOL_CALL_CHECK_AFTER_COMPACTION,
    TEMPERATURE,
)
from holmes.core.issue import Issue
from holmes.core.llm import LLM
from holmes.core.llm_usage import extract_usage_from_response
from holmes.core.models import (
    PendingToolApproval,
    ToolApprovalDecision,
    ToolCallResult,
)
from holmes.core.prompt import generate_user_prompt
from holmes.core.safeguards import prevent_overly_repeated_tool_call
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
)
from holmes.core.tools_utils.tool_context_window_limiter import (
    prevent_overly_big_tool_response,
)
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.core.tracing import DummySpan
from holmes.core.truncation.input_context_window_limiter import (
    limit_input_context_window,
)
from holmes.utils.colors import AI_COLOR
from holmes.utils.stream import (
    StreamEvents,
    StreamMessage,
    add_token_count_to_metadata,
    build_stream_event_token_count,
)
from holmes.utils.tags import parse_messages_tags

class LLMInterruptedError(Exception):
    """Raised when the user interrupts an in-progress LLM call (e.g. via Escape key)."""

    pass


# Create a named logger for cost tracking
cost_logger = logging.getLogger("holmes.costs")


def _extract_text_from_content(content: Any) -> str:
    """Extract text from message content, handling both string and array formats.

    OpenAI/LiteLLM message content can be:
    - A plain string: "some text"
    - An array of content objects: [{"type": "text", "text": "some text", ...}]

    The array format is used by our cache_control feature (see llm.py add_cache_control_to_last_message)
    which converts string content to a single-item array. For tool messages, there's always
    only one text item containing the full tool output with tool_call_metadata at the start.

    Args:
        content: Message content (string or array)

    Returns:
        Extracted text as a string
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        # Tool messages have a single text item (created by format_tool_result_data,
        # possibly wrapped in array by cache_control). Return the first text item.
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                return item.get("text", "")

    return ""


def extract_bash_session_prefixes(messages: List[Dict[str, Any]]) -> List[str]:
    """Extract bash session approved prefixes from conversation history.

    Scans tool result messages for bash_session_approved_prefixes stored in
    tool_call_metadata. These prefixes were approved by the user via the
    "Yes, and don't ask again" option.

    Args:
        messages: Conversation history messages

    Returns:
        List of approved prefixes accumulated from all tool results
    """
    prefixes: set[str] = set()

    for msg in messages:
        if msg.get("role") != "tool":
            continue

        content = _extract_text_from_content(msg.get("content", ""))
        if not content:
            continue

        # Extract tool_call_metadata from the content string
        # Format: tool_call_metadata={"tool_name": "...", ...}
        match = re.search(r"tool_call_metadata=(\{[^}]+\})", content)
        if not match:
            continue

        try:
            metadata = json.loads(match.group(1))
            if "bash_session_approved_prefixes" in metadata:
                prefixes.update(metadata["bash_session_approved_prefixes"])
        except (json.JSONDecodeError, KeyError):
            continue

    if prefixes:
        logging.info(
            f"Found {len(prefixes)} session-approved bash prefixes from conversation: {list(prefixes)}"
        )
    return list(prefixes)


class LLMCosts(BaseModel):
    """Tracks cost and token usage for LLM calls."""

    total_cost: float = 0.0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: Optional[int] = None
    reasoning_tokens: int = 0
    max_completion_tokens_per_call: int = 0
    num_compactions: int = 0


def _process_cost_info(
    full_response, costs: Optional[LLMCosts] = None, log_prefix: str = "LLM call"
) -> None:
    """Process cost and token information from LLM response.

    Logs the cost information and optionally accumulates it into a costs object.

    Args:
        full_response: The raw LLM response object
        costs: Optional LLMCosts object to accumulate costs into
        log_prefix: Prefix for logging messages (e.g., "LLM call", "Post-processing")
    """
    try:
        raw = extract_usage_from_response(full_response)

        if LOG_LLM_USAGE_RESPONSE:
            usage = getattr(full_response, "usage", None)
            if usage:
                logging.info(f"LLM usage response:\n{usage}\n")

        if raw.total_tokens > 0:
            cost_logger.debug(
                f"{log_prefix} cost: ${raw.cost:.6f} | Tokens: {raw.prompt_tokens} prompt + {raw.completion_tokens} completion = {raw.total_tokens} total"
            )
            if costs:
                costs.total_cost += raw.cost
                costs.prompt_tokens += raw.prompt_tokens
                costs.completion_tokens += raw.completion_tokens
                costs.total_tokens += raw.total_tokens
                if raw.cached_tokens is not None:
                    costs.cached_tokens = (costs.cached_tokens or 0) + raw.cached_tokens
                costs.reasoning_tokens += raw.reasoning_tokens
                costs.max_completion_tokens_per_call = max(
                    costs.max_completion_tokens_per_call, raw.completion_tokens
                )
        elif raw.cost > 0:
            cost_logger.debug(
                f"{log_prefix} cost: ${raw.cost:.6f} | Token usage not available"
            )
            if costs:
                costs.total_cost += raw.cost
    except (AttributeError, TypeError, KeyError) as e:
        logging.debug(f"Could not extract cost information: {e}")


class LLMResult(LLMCosts):
    tool_calls: Optional[List[ToolCallResult]] = None
    num_llm_calls: Optional[int] = None  # Number of LLM API calls (turns)
    result: Optional[str] = None
    unprocessed_result: Optional[str] = None
    instructions: List[str] = Field(default_factory=list)
    # TODO: clean up these two
    prompt: Optional[str] = None
    messages: Optional[List[dict]] = None
    metadata: Optional[Dict[Any, Any]] = None

    def get_tool_usage_summary(self):
        return "AI used info from issue and " + ",".join(
            [f"`{tool_call.description}`" for tool_call in self.tool_calls]
        )


class ToolCallWithDecision(BaseModel):
    message_index: int
    tool_call: ChatCompletionMessageToolCall
    decision: Optional[ToolApprovalDecision]


class ToolCallingLLM:
    llm: LLM

    def __init__(
        self,
        tool_executor: ToolExecutor,
        max_steps: int,
        llm: LLM,
        tool_results_dir: Optional[Path],
        tracer=None,
    ):
        self.tool_executor = tool_executor
        self.max_steps = max_steps
        self.tracer = tracer
        self.llm = llm
        self.tool_results_dir = tool_results_dir
        self.approval_callback: Optional[
            Callable[[StructuredToolResult], tuple[bool, Optional[str]]]
        ] = None

        self._runbook_in_use: bool = False

    def reset_interaction_state(self) -> None:
        """
        For interactive loop, reset runbooks in use
        """
        self._runbook_in_use = False

    def _has_bash_for_file_access(self) -> bool:
        """Check if bash toolset is available for reading saved tool result files."""
        for toolset in self.tool_executor.enabled_toolsets:
            if toolset.name == "bash":
                config = toolset.config
                if config and hasattr(config, "include_default_allow_deny_list"):
                    return config.include_default_allow_deny_list
                return False
        return False

    def process_tool_decisions(
        self,
        messages: List[Dict[str, Any]],
        tool_decisions: List[ToolApprovalDecision],
        request_context: Optional[Dict[str, Any]] = None,
    ) -> tuple[List[Dict[str, Any]], list[StreamMessage]]:
        """
        Process tool approval decisions and execute approved tools.

        Args:
            messages: Current conversation messages
            tool_decisions: List of ToolApprovalDecision objects

        Returns:
            Updated messages list with tool execution results
        """
        events: list[StreamMessage] = []
        if not tool_decisions:
            return messages, events

        # Create decision lookup
        decisions_by_tool_call_id = {
            decision.tool_call_id: decision for decision in tool_decisions
        }

        pending_tool_calls: list[ToolCallWithDecision] = []

        for i in reversed(range(len(messages))):
            msg = messages[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                message_tool_calls = msg.get("tool_calls", [])
                for tool_call in message_tool_calls:
                    decision = decisions_by_tool_call_id.get(tool_call.get("id"), None)
                    if tool_call.get("pending_approval"):
                        del tool_call[
                            "pending_approval"
                        ]  # Cleanup so that a pending approval is not tagged on message in a future response
                        pending_tool_calls.append(
                            ToolCallWithDecision(
                                tool_call=ChatCompletionMessageToolCall(**tool_call),
                                decision=decision,
                                message_index=i,
                            )
                        )

        if not pending_tool_calls:
            error_message = f"Received {len(tool_decisions)} tool decisions but no pending approvals found"
            logging.error(error_message)
            raise Exception(error_message)
        # Extract existing session prefixes from conversation history
        session_prefixes = extract_bash_session_prefixes(messages)

        for tool_call_with_decision in pending_tool_calls:
            tool_call_message: dict
            tool_call = tool_call_with_decision.tool_call
            decision = tool_call_with_decision.decision
            tool_result: Optional[ToolCallResult] = None
            if decision and decision.approved:
                tool_result = self._invoke_llm_tool_call(
                    tool_to_call=tool_call,
                    previous_tool_calls=[],
                    trace_span=DummySpan(),  # TODO: replace with proper span
                    tool_number=None,
                    user_approved=True,
                    session_approved_prefixes=session_prefixes,
                    request_context=request_context,
                )
            else:
                # Tool was rejected or no decision found, add rejection message
                tool_result = ToolCallResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.function.name,
                    description=tool_call.function.name,
                    result=StructuredToolResult(
                        status=StructuredToolResultStatus.ERROR,
                        error="Tool execution was denied by the user.",
                    ),
                )

            events.append(
                StreamMessage(
                    event=StreamEvents.TOOL_RESULT,
                    data=tool_result.as_streaming_tool_result_response(),
                )
            )

            # If user chose "Yes, and don't ask again", include prefixes in metadata
            extra_metadata = None
            if decision and decision.approved and decision.save_prefixes:
                logging.info(
                    f"Saving bash session prefixes for future commands: {decision.save_prefixes}"
                )
                extra_metadata = {
                    "bash_session_approved_prefixes": decision.save_prefixes
                }

            tool_call_message = tool_result.as_tool_call_message(
                extra_metadata=extra_metadata
            )

            # It is expected that the tool call result directly follows the tool call request from the LLM
            # The API call may contain a user ask which is appended to the messages so we can't just append
            # tool call results; they need to be inserted right after the llm's message requesting tool calls
            messages.insert(
                tool_call_with_decision.message_index + 1, tool_call_message
            )

        return messages, events

    def prompt_call(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: Optional[Union[dict, Type[BaseModel]]] = None,
        trace_span: Optional[Any] = None,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> LLMResult:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self.call(
            messages,
            response_format=response_format,
            trace_span=trace_span,
            request_context=request_context,
        )

    def messages_call(
        self,
        messages: List[Dict[str, str]],
        response_format: Optional[Union[dict, Type[BaseModel]]] = None,
        trace_span: Optional[Any] = None,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> LLMResult:
        return self.call(
            messages,
            response_format=response_format,
            trace_span=trace_span,
            request_context=request_context,
        )

    def _should_include_restricted_tools(self) -> bool:
        """Check if restricted tools should be included in the tools list."""
        return self._runbook_in_use

    def _get_tools(self) -> list:
        """Get tools list, filtering restricted tools based on authorization."""
        return self.tool_executor.get_all_tools_openai_format(
            target_model=self.llm.model,
            include_restricted=self._should_include_restricted_tools(),
        )

    def _run_loop(
        self,
        messages: List[Dict],
        response_format: Optional[Union[dict, Type[BaseModel]]] = None,
        trace_span: Optional[Any] = None,
        tool_number_offset: int = 0,
        request_context: Optional[Dict[str, Any]] = None,
        cancel_event: Optional[threading.Event] = None,
        enable_tool_approval: bool = False,
    ) -> Generator[AgentEvent, None, None]:
        """Core agent loop that yields typed AgentEvent objects.

        Both call() and call_stream() delegate to this generator.
        The caller is responsible for mapping events to their output format.
        """
        if trace_span is None:
            trace_span = DummySpan()
        tool_calls: list[dict] = []  # For preventing repeated tool calls; reset after compaction
        all_tool_calls: list = []
        costs = LLMCosts()
        tools: Optional[list] = self._get_tools()
        max_steps = self.max_steps
        metadata: Dict[Any, Any] = {}
        i = 0

        # Extract session approved prefixes from conversation history
        session_prefixes = extract_bash_session_prefixes(messages)

        while i < max_steps:
            if cancel_event and cancel_event.is_set():
                raise LLMInterruptedError()

            i += 1
            logging.debug(f"running iteration {i}")

            yield IterationStartEvent(iteration=i, max_steps=max_steps)

            # On the last step we don't allow tools - force a reply
            tools = None if i == max_steps else tools
            tool_choice = "auto" if tools else None

            limit_result = limit_input_context_window(
                llm=self.llm, messages=messages, tools=tools
            )
            messages = limit_result.messages
            metadata = metadata | limit_result.metadata

            # Accumulate compaction tokens/cost
            compaction = limit_result.compaction_usage
            if compaction.total_tokens > 0:
                costs.num_compactions += 1
                costs.total_tokens += compaction.total_tokens
                costs.prompt_tokens += compaction.prompt_tokens
                costs.completion_tokens += compaction.completion_tokens
                costs.total_cost += compaction.cost
                cost_logger.debug(
                    f"Compaction cost: ${compaction.cost:.6f} | "
                    f"Tokens: {compaction.prompt_tokens} prompt + {compaction.completion_tokens} completion = {compaction.total_tokens} total"
                )

            if limit_result.events or limit_result.conversation_history_compacted:
                yield CompactionEvent(
                    metadata=metadata.copy(),
                    stream_events=limit_result.events,
                )

            if (
                limit_result.conversation_history_compacted
                and RESET_REPEATED_TOOL_CALL_CHECK_AFTER_COMPACTION
            ):
                tool_calls = []

            logging.debug(f"sending messages={messages}\n\ntools={tools}")

            try:
                full_response = self.llm.completion(
                    messages=parse_messages_tags(messages),
                    tools=tools,
                    tool_choice=tool_choice,
                    temperature=TEMPERATURE,
                    response_format=response_format,
                    stream=False,
                    drop_params=True,
                )
                logging.debug(f"got response {full_response.to_json()}")  # type: ignore

                _process_cost_info(full_response, costs, "LLM call")

            except BadRequestError as e:
                if "Unrecognized request arguments supplied: tool_choice, tools" in str(e):
                    raise Exception(
                        "The Azure model you chose is not supported. Model version 1106 and higher required."
                    ) from e
                else:
                    logging.error(
                        f"LLM BadRequestError on model={self.llm.model} (iteration {i}): {e}",
                        exc_info=True,
                    )
                    raise
            except Exception as e:
                logging.error(
                    f"LLM call failed on model={self.llm.model} (iteration {i}): "
                    f"{type(e).__name__}: {e}",
                    exc_info=True,
                )
                raise

            if cancel_event and cancel_event.is_set():
                raise LLMInterruptedError()

            response_message = full_response.choices[0].message  # type: ignore

            new_message = response_message.model_dump(
                exclude_defaults=True, exclude_unset=True, exclude_none=True
            )
            messages.append(new_message)

            tools_to_call = getattr(response_message, "tool_calls", None)
            text_response = response_message.content
            reasoning = getattr(response_message, "reasoning_content", None)

            # Token counting after LLM response
            tokens = self.llm.count_tokens(messages=messages, tools=tools)
            add_token_count_to_metadata(
                tokens=tokens,
                full_llm_response=full_response,
                max_context_size=limit_result.max_context_size,
                maximum_output_token=limit_result.maximum_output_token,
                metadata=metadata,
            )
            metadata["costs"] = costs.model_dump()
            yield TokenCountEvent(metadata=metadata.copy())

            if not tools_to_call:
                yield LLMResponseEvent(
                    content=text_response,
                    reasoning_content=reasoning,
                    has_tool_calls=False,
                    iteration=i,
                )
                yield CompletionEvent(
                    result=text_response,
                    messages=messages,
                    tool_calls=all_tool_calls,
                    metadata=metadata,
                    num_llm_calls=i,
                )
                return

            # There are tool calls
            yield LLMResponseEvent(
                content=text_response,
                reasoning_content=reasoning,
                has_tool_calls=True,
                iteration=i,
            )

            if text_response and text_response.strip():
                logging.info(f"[bold {AI_COLOR}]AI:[/bold {AI_COLOR}] {text_response}")
            logging.info(
                f"The AI requested [bold]{len(tools_to_call) if tools_to_call else 0}[/bold] tool call(s)."
            )

            pending_approvals = []
            approval_required_tools = []

            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                futures = []
                futures_tool_numbers: dict[concurrent.futures.Future, Optional[int]] = {}
                for tool_index, t in enumerate(tools_to_call, 1):
                    logging.debug(f"Tool to call: {t}")
                    tool_number: Optional[int] = tool_number_offset + tool_index

                    yield ToolStartEvent(
                        tool_call_id=t.id,
                        tool_name=t.function.name,
                        tool_number=tool_number,
                    )

                    future = executor.submit(
                        self._invoke_llm_tool_call,
                        tool_to_call=t,
                        previous_tool_calls=tool_calls,
                        trace_span=trace_span,
                        tool_number=tool_number,
                        session_approved_prefixes=session_prefixes,
                        request_context=request_context,
                    )
                    futures_tool_numbers[future] = tool_number
                    futures.append(future)

                for future in concurrent.futures.as_completed(futures):
                    if cancel_event and cancel_event.is_set():
                        for f in futures:
                            f.cancel()
                        raise LLMInterruptedError()

                    tool_call_result: ToolCallResult = future.result()
                    tool_number = futures_tool_numbers.get(future)

                    if (
                        tool_call_result.result.status
                        == StructuredToolResultStatus.APPROVAL_REQUIRED
                    ):
                        if enable_tool_approval:
                            # Streaming mode: collect for ApprovalRequiredEvent
                            pending_approvals.append(
                                PendingToolApproval(
                                    tool_call_id=tool_call_result.tool_call_id,
                                    tool_name=tool_call_result.tool_name,
                                    description=tool_call_result.description,
                                    params=tool_call_result.result.params or {},
                                )
                            )
                            approval_required_tools.append(tool_call_result)
                            yield ToolResultEvent(tool_call_result=tool_call_result)
                        else:
                            # Non-streaming: handle approval via callback or convert to error
                            tool_call_result = self._handle_tool_call_approval(
                                tool_call_result=tool_call_result,
                                tool_number=tool_number,
                                trace_span=trace_span,
                                request_context=request_context,
                            )
                            tool_result_response_dict = tool_call_result.as_tool_result_response()
                            tool_calls.append(tool_result_response_dict)
                            all_tool_calls.append(tool_result_response_dict)
                            messages.append(tool_call_result.as_tool_call_message())
                            yield ToolResultEvent(tool_call_result=tool_call_result)
                    else:
                        tool_result_response_dict = tool_call_result.as_tool_result_response()
                        tool_calls.append(tool_result_response_dict)
                        all_tool_calls.append(tool_result_response_dict)
                        messages.append(tool_call_result.as_tool_call_message())
                        yield ToolResultEvent(tool_call_result=tool_call_result)

                # Token counting after tool results
                tokens = self.llm.count_tokens(messages=messages, tools=tools)
                add_token_count_to_metadata(
                    tokens=tokens,
                    full_llm_response=full_response,
                    max_context_size=limit_result.max_context_size,
                    maximum_output_token=limit_result.maximum_output_token,
                    metadata=metadata,
                )
                metadata["costs"] = costs.model_dump()
                yield TokenCountEvent(metadata=metadata.copy())

                # Update the tool number offset for the next iteration
                tool_number_offset += len(tools_to_call)

                if pending_approvals:
                    for result in approval_required_tools:
                        tool_call = self.find_assistant_tool_call_request(
                            tool_call_id=result.tool_call_id, messages=messages
                        )
                        tool_call["pending_approval"] = True

                    yield ApprovalRequiredEvent(
                        pending_approvals=pending_approvals,
                        approval_required_tools=approval_required_tools,
                        messages=messages,
                        metadata=metadata,
                        tool_number_offset=tool_number_offset,
                    )
                    return

                # Re-fetch tools if runbook was just activated (enables restricted tools)
                if self._runbook_in_use and tools is not None:
                    new_tools = self._get_tools()
                    if len(new_tools) != len(tools):
                        logging.info(
                            f"Runbook activated - refreshing tools list ({len(tools)} -> {len(new_tools)} tools)"
                        )
                        tools = new_tools

                # Re-extract session prefixes (a tool may have added new approved prefixes)
                session_prefixes = extract_bash_session_prefixes(messages)

                # Add a blank line after all tools in this batch complete
                if tools_to_call:
                    logging.info("")

        raise Exception(f"Too many LLM calls - exceeded max_steps: {i}/{max_steps}")

    @sentry_sdk.trace
    def call(  # type: ignore
        self,
        messages: List[Dict[str, str]],
        response_format: Optional[Union[dict, Type[BaseModel]]] = None,
        trace_span: Optional[Any] = None,
        tool_number_offset: int = 0,
        request_context: Optional[Dict[str, Any]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> LLMResult:
        if trace_span is None:
            trace_span = DummySpan()
        for event in self._run_loop(
            messages=messages,
            response_format=response_format,
            trace_span=trace_span,
            tool_number_offset=tool_number_offset,
            request_context=request_context,
            cancel_event=cancel_event,
            enable_tool_approval=False,
        ):
            if isinstance(event, LLMResponseEvent):
                if event.reasoning_content:
                    logging.info(
                        f"[italic dim]AI reasoning:\n\n{event.reasoning_content}[/italic dim]\n"
                    )

            elif isinstance(event, CompletionEvent):
                costs_dict = event.metadata.get("costs", {})
                return LLMResult(
                    result=event.result,
                    tool_calls=event.tool_calls,
                    num_llm_calls=event.num_llm_calls,
                    prompt=json.dumps(event.messages, indent=2),
                    messages=event.messages,
                    metadata=event.metadata,
                    **costs_dict,
                )

        raise Exception("Agent loop ended without completion")

    def _directly_invoke_tool_call(
        self,
        tool_name: str,
        tool_params: dict,
        user_approved: bool,
        tool_call_id: str,
        tool_number: Optional[int] = None,
        session_approved_prefixes: Optional[List[str]] = None,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> StructuredToolResult:
        # Ensure the toolset is initialized (lazy initialization on first use)
        init_error = self.tool_executor.ensure_toolset_initialized(tool_name)
        if isinstance(init_error, str):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=init_error,
                params=tool_params,
            )

        tool = self.tool_executor.get_tool_by_name(tool_name)
        if not tool:
            logging.warning(
                f"Skipping tool execution for {tool_name}: args: {tool_params}"
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to find tool {tool_name}",
                params=tool_params,
            )

        try:
            invoke_context = ToolInvokeContext(
                tool_number=tool_number,
                user_approved=user_approved,
                llm=self.llm,
                max_token_count=self.llm.get_max_token_count_for_single_tool(),
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                session_approved_prefixes=session_approved_prefixes or [],
                request_context=request_context,
            )
            tool_response = tool.invoke(tool_params, context=invoke_context)

            # Track runbook usage - if fetch_runbook is called successfully,
            # restricted tools become available for the rest of the current request
            if (
                tool_name == "fetch_runbook"
                and tool_response.status == StructuredToolResultStatus.SUCCESS
            ):
                self._runbook_in_use = True
                logging.debug("Runbook fetched - restricted tools now available")

        except Exception as e:
            logging.error(
                f"Tool call to {tool_name} failed with an Exception", exc_info=True
            )
            tool_response = StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Tool call failed: {e}",
                params=tool_params,
            )
        return tool_response

    def _get_tool_call_result(
        self,
        tool_call_id: str,
        tool_name: str,
        tool_arguments: str,
        user_approved: bool,
        previous_tool_calls: list[dict],
        tool_number: Optional[int] = None,
        session_approved_prefixes: Optional[List[str]] = None,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> ToolCallResult:
        tool_params = {}
        try:
            tool_params = json.loads(tool_arguments)
        except Exception:
            logging.warning(
                f"Failed to parse arguments for tool: {tool_name}. args: {tool_arguments}"
            )

        tool_response = None
        if not user_approved:
            tool_response = prevent_overly_repeated_tool_call(
                tool_name=tool_name,
                tool_params=tool_params,
                tool_calls=previous_tool_calls,
            )

        if not tool_response:
            tool_response = self._directly_invoke_tool_call(
                tool_name=tool_name,
                tool_params=tool_params,
                user_approved=user_approved,
                tool_number=tool_number,
                tool_call_id=tool_call_id,
                session_approved_prefixes=session_approved_prefixes,
                request_context=request_context,
            )

        if not isinstance(tool_response, StructuredToolResult):
            # Should never be needed but ensure Holmes does not crash if one of the tools does not return the right type
            logging.error(
                f"Tool {tool_name} return type is not StructuredToolResult. Nesting the tool result into StructuredToolResult..."
            )
            tool_response = StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=tool_response,
                params=tool_params,
            )

        tool = self.tool_executor.get_tool_by_name(tool_name)

        return ToolCallResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            description=str(tool.get_parameterized_one_liner(tool_params))
            if tool
            else "",
            result=tool_response,
        )

    @staticmethod
    def _log_tool_call_result(
        tool_span,
        tool_call_result: ToolCallResult,
        approval_possible=True,
        original_token_count=None,
    ):
        tool_span.set_attributes(name=tool_call_result.tool_name)
        status = tool_call_result.result.status

        if (
            status == StructuredToolResultStatus.APPROVAL_REQUIRED
            and not approval_possible
        ):
            status = StructuredToolResultStatus.ERROR

        if status == StructuredToolResultStatus.ERROR:
            error = (
                tool_call_result.result.error
                if tool_call_result.result.error
                else "Unspecified error"
            )
        else:
            error = None
        tool_span.log(
            input=tool_call_result.result.params,
            output=tool_call_result.result.data,
            error=error,
            metadata={
                "status": status,
                "description": tool_call_result.description,
                "return_code": tool_call_result.result.return_code,
                "error": tool_call_result.result.error,
                "original_token_count": original_token_count,
            },
        )

    def _invoke_llm_tool_call(
        self,
        tool_to_call: ChatCompletionMessageToolCall,
        previous_tool_calls: list[dict],
        trace_span=None,
        tool_number=None,
        user_approved: bool = False,
        session_approved_prefixes: Optional[List[str]] = None,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> ToolCallResult:
        if trace_span is None:
            trace_span = DummySpan()
        with trace_span.start_span(type="tool") as tool_span:
            if not hasattr(tool_to_call, "function"):
                # Handle the union type - ChatCompletionMessageToolCall can be either
                # ChatCompletionMessageFunctionToolCall (with 'function' field and type='function')
                # or ChatCompletionMessageCustomToolCall (with 'custom' field and type='custom').
                # We use hasattr to check for the 'function' attribute as it's more flexible
                # and doesn't require importing the specific type.
                tool_name = "Unknown_Custom_Tool"
                logging.error(f"Unsupported custom tool call: {tool_to_call}")
                tool_call_result = ToolCallResult(
                    tool_call_id=tool_to_call.id,
                    tool_name=tool_name,
                    description="NA",
                    result=StructuredToolResult(
                        status=StructuredToolResultStatus.ERROR,
                        error="Custom tool calls are not supported",
                        params=None,
                    ),
                )
            else:
                tool_name = tool_to_call.function.name
                tool_arguments = tool_to_call.function.arguments
                tool_id = tool_to_call.id
                tool_call_result = self._get_tool_call_result(
                    tool_id,
                    tool_name,
                    tool_arguments,
                    previous_tool_calls=previous_tool_calls,
                    tool_number=tool_number,
                    user_approved=user_approved,
                    session_approved_prefixes=session_approved_prefixes,
                    request_context=request_context,
                )

            original_token_count = prevent_overly_big_tool_response(
                tool_call_result=tool_call_result,
                llm=self.llm,
                tool_results_dir=self.tool_results_dir
                if self.tool_results_dir and self._has_bash_for_file_access()
                else None,
            )

            ToolCallingLLM._log_tool_call_result(
                tool_span,
                tool_call_result,
                self.approval_callback is not None,
                original_token_count,
            )
            return tool_call_result

    def _is_tool_call_already_approved(self, tool_call_result):
        tool = self.tool_executor.get_tool_by_name(tool_call_result.tool_name)
        if not tool:
            return False
        context = ToolInvokeContext(
            llm=self.llm,
            max_token_count=self.llm.get_max_token_count_for_single_tool(),
            tool_name=tool_call_result.tool_name,
            tool_call_id=tool_call_result.tool_call_id,
        )
        approval = tool.requires_approval(tool_call_result.result.params or {}, context)
        return not approval or not approval.needs_approval

    def _handle_tool_call_approval(
        self,
        tool_call_result: ToolCallResult,
        tool_number: Optional[int],
        trace_span: Any,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> ToolCallResult:
        """
        Handle approval for a single tool call if required.

        Args:
            tool_call_result: A single tool call result that may require approval
            tool_number: The tool call number

        Returns:
            Updated tool call result with approved/denied status
        """

        # If no approval callback, convert to ERROR because it is assumed the client may not be able to handle approvals
        if not self.approval_callback:
            tool_call_result.result.status = StructuredToolResultStatus.ERROR
            return tool_call_result

        # Re-check if approval is still needed (prefix may have been approved by another tool call)
        if self._is_tool_call_already_approved(tool_call_result):
            logging.info(f"Approval no longer needed for {tool_call_result.tool_name}")
            with trace_span.start_span(type="tool") as tool_span:
                tool_call_result.result = self._directly_invoke_tool_call(
                    tool_name=tool_call_result.tool_name,
                    tool_params=tool_call_result.result.params or {},
                    user_approved=False,
                    tool_number=tool_number,
                    tool_call_id=tool_call_result.tool_call_id,
                    request_context=request_context,
                )
                ToolCallingLLM._log_tool_call_result(tool_span, tool_call_result)
            return tool_call_result

        # Get approval from user
        with trace_span.start_span(
            type="task", name=f"Ask approval for {tool_call_result.tool_name}"
        ):
            approved, feedback = self.approval_callback(tool_call_result.result)

        # Note - Tool calls are currently logged twice, once when returning APPROVAL_REQUIRED and once here
        with trace_span.start_span(type="tool") as tool_span:
            if approved:
                logging.debug(
                    f"User approved command: {tool_call_result.result.invocation}"
                )
                new_response = self._directly_invoke_tool_call(
                    tool_name=tool_call_result.tool_name,
                    tool_params=tool_call_result.result.params or {},
                    user_approved=True,
                    tool_number=tool_number,
                    tool_call_id=tool_call_result.tool_call_id,
                    request_context=request_context,
                )
                tool_call_result.result = new_response
            else:
                # User denied - update to error
                feedback_text = f" User feedback: {feedback}" if feedback else ""
                tool_call_result.result.status = StructuredToolResultStatus.ERROR
                tool_call_result.result.error = (
                    f"User denied command execution.{feedback_text}"
                )
            ToolCallingLLM._log_tool_call_result(tool_span, tool_call_result)

        return tool_call_result

    def call_stream(
        self,
        system_prompt: str = "",
        user_prompt: Optional[str] = None,
        response_format: Optional[Union[dict, Type[BaseModel]]] = None,
        msgs: Optional[list[dict]] = None,
        enable_tool_approval: bool = False,
        tool_decisions: List[ToolApprovalDecision] | None = None,
        request_context: Optional[Dict[str, Any]] = None,
    ):
        """
        Streams holmes one iteration at a time by delegating to _run_loop()
        and mapping AgentEvents to StreamMessages.
        """
        # Process tool decisions if provided
        if msgs and tool_decisions:
            logging.info(f"Processing {len(tool_decisions)} tool decisions")
            msgs, events = self.process_tool_decisions(
                msgs, tool_decisions, request_context
            )
            yield from events

        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if user_prompt:
            messages.append({"role": "user", "content": user_prompt})
        if msgs:
            messages.extend(msgs)

        for event in self._run_loop(
            messages=messages,
            response_format=response_format,
            request_context=request_context,
            enable_tool_approval=enable_tool_approval,
        ):
            if isinstance(event, CompactionEvent):
                # Forward the original StreamMessage events from the context window limiter
                yield from event.stream_events

            elif isinstance(event, TokenCountEvent):
                yield build_stream_event_token_count(metadata=event.metadata)

            elif isinstance(event, LLMResponseEvent):
                if not event.has_tool_calls:
                    # No tool calls = final answer, don't yield AI_MESSAGE here
                    # CompletionEvent will follow and yield ANSWER_END
                    pass
                else:
                    # Intermediate message with tool calls coming
                    if event.content or event.reasoning_content:
                        yield StreamMessage(
                            event=StreamEvents.AI_MESSAGE,
                            data={
                                "content": event.content,
                                "reasoning": event.reasoning_content,
                                "metadata": {},
                            },
                        )

            elif isinstance(event, ToolStartEvent):
                yield StreamMessage(
                    event=StreamEvents.START_TOOL,
                    data={"tool_name": event.tool_name, "id": event.tool_call_id},
                )

            elif isinstance(event, ToolResultEvent):
                yield StreamMessage(
                    event=StreamEvents.TOOL_RESULT,
                    data=event.tool_call_result.as_streaming_tool_result_response(),
                )

            elif isinstance(event, CompletionEvent):
                yield StreamMessage(
                    event=StreamEvents.ANSWER_END,
                    data={
                        "content": event.result,
                        "messages": event.messages,
                        "metadata": event.metadata,
                    },
                )
                return

            elif isinstance(event, ApprovalRequiredEvent):
                yield StreamMessage(
                    event=StreamEvents.APPROVAL_REQUIRED,
                    data={
                        "content": None,
                        "messages": event.messages,
                        "pending_approvals": [
                            approval.model_dump()
                            for approval in event.pending_approvals
                        ],
                        "requires_approval": True,
                    },
                )
                return

    def find_assistant_tool_call_request(
        self, tool_call_id: str, messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        for message in messages:
            if message.get("role") == "assistant":
                for tool_call in message.get("tool_calls", []):
                    if tool_call.get("id") == tool_call_id:
                        return tool_call

        # Should not happen unless there is a bug.
        # If we are here
        raise Exception(
            f"Failed to find assistant request for a tool_call in conversation history. tool_call_id={tool_call_id}"
        )
