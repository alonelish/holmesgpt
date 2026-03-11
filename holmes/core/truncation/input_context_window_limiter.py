import logging
import time
from typing import Any, Generator, Optional

import sentry_sdk
from pydantic import BaseModel

from holmes.common.env_vars import (
    ENABLE_CONVERSATION_HISTORY_COMPACTION,
    MAX_OUTPUT_TOKEN_RESERVATION,
)
from holmes.core.llm import (
    LLM,
    TokenCountMetadata,
    get_context_window_compaction_threshold_pct,
)
from holmes.core.models import TruncationMetadata, TruncationResult
from holmes.core.truncation.compaction import CompactionUsage, compact_conversation_history, compute_conversation_stats
from holmes.utils import sentry_helper
from holmes.utils.stream import StreamEvents, StreamMessage

TRUNCATION_NOTICE = "\n\n[TRUNCATED]"


def _truncate_tool_message(
    msg: dict, allocated_space: int, needed_space: int
) -> TruncationMetadata:
    msg_content = msg["content"]
    tool_call_id = msg["tool_call_id"]
    tool_name = msg["name"]

    # Ensure the indicator fits in the allocated space
    if allocated_space > len(TRUNCATION_NOTICE):
        original = msg_content if isinstance(msg_content, str) else str(msg_content)
        msg["content"] = (
            original[: allocated_space - len(TRUNCATION_NOTICE)] + TRUNCATION_NOTICE
        )
        end_index = allocated_space - len(TRUNCATION_NOTICE)
    else:
        msg["content"] = TRUNCATION_NOTICE[:allocated_space]
        end_index = allocated_space

    msg.pop("token_count", None)  # Remove token_count if present
    logging.info(
        f"Truncating tool message '{tool_name}' from {needed_space} to {allocated_space} tokens"
    )
    truncation_metadata = TruncationMetadata(
        tool_call_id=tool_call_id,
        start_index=0,
        end_index=end_index,
        tool_name=tool_name,
        original_token_count=needed_space,
    )
    return truncation_metadata


# TODO: I think there's a bug here because we don't account for the 'role' or json structure like '{...}' when counting tokens
# However, in practice it works because we reserve enough space for the output tokens that the minor inconsistency does not matter
# We should fix this in the future
# TODO: we truncate using character counts not token counts - this means we're overly agressive with truncation - improve it by considering
# token truncation and not character truncation
def truncate_messages_to_fit_context(
    messages: list, max_context_size: int, maximum_output_token: int, count_tokens_fn
) -> TruncationResult:
    """
    Helper function to truncate tool messages to fit within context limits.

    Args:
        messages: List of message dictionaries with roles and content
        max_context_size: Maximum context window size for the model
        maximum_output_token: Maximum tokens reserved for model output
        count_tokens_fn: Function to count tokens for a list of messages

    Returns:
        Modified list of messages with truncated tool responses

    Raises:
        Exception: If non-tool messages exceed available context space
    """
    messages_except_tools = [
        message for message in messages if message["role"] != "tool"
    ]
    tokens = count_tokens_fn(messages_except_tools)
    message_size_without_tools = tokens.total_tokens

    tool_call_messages = [message for message in messages if message["role"] == "tool"]

    reserved_for_output_tokens = min(maximum_output_token, MAX_OUTPUT_TOKEN_RESERVATION)
    if message_size_without_tools >= (max_context_size - reserved_for_output_tokens):
        logging.error(
            f"The combined size of system_prompt and user_prompt ({message_size_without_tools} tokens) exceeds the model's context window for input."
        )
        raise Exception(
            f"The combined size of system_prompt and user_prompt ({message_size_without_tools} tokens) exceeds the maximum context size of {max_context_size - reserved_for_output_tokens} tokens available for input."
        )

    if len(tool_call_messages) == 0:
        return TruncationResult(truncated_messages=messages, truncations=[])

    available_space = (
        max_context_size - message_size_without_tools - reserved_for_output_tokens
    )
    remaining_space = available_space
    t_sort = time.monotonic()
    tool_call_messages.sort(
        key=lambda x: x.get("token_count") or count_tokens_fn(
            [{"role": "tool", "content": x["content"]}]
        ).total_tokens
    )
    logging.debug(f"truncate_messages: sort {len(tool_call_messages)} tool msgs took {(time.monotonic() - t_sort) * 1000:.1f}ms")

    truncations = []

    # Allocate space starting with small tools and going to larger tools, while maintaining fairness
    # Small tools can often get exactly what they need, while larger tools may need to be truncated
    # We ensure fairness (no tool gets more than others that need it) and also maximize utilization (we don't leave space unused)
    for i, msg in enumerate(tool_call_messages):
        remaining_tools = len(tool_call_messages) - i
        max_allocation = remaining_space // remaining_tools
        needed_space = msg.get("token_count") or count_tokens_fn(
            [{"role": "tool", "content": msg["content"]}]
        ).total_tokens
        allocated_space = min(needed_space, max_allocation)

        if needed_space > allocated_space:
            truncation_metadata = _truncate_tool_message(
                msg, allocated_space, needed_space
            )
            truncations.append(truncation_metadata)

        remaining_space -= allocated_space

    if truncations:
        sentry_helper.capture_tool_truncations(truncations)

    return TruncationResult(truncated_messages=messages, truncations=truncations)


class CompactionOutput(BaseModel):
    """Mutable result populated by compact_if_needed."""

    messages: list[dict] = []
    compacted: bool = False
    usage: CompactionUsage = CompactionUsage()


def compact_if_needed(
    llm: LLM,
    messages: list[dict],
    tools: Optional[list[dict[str, Any]]],
    initial_tokens: TokenCountMetadata,
    max_context_size: int,
    maximum_output_token: int,
) -> Generator[StreamMessage, None, CompactionOutput]:
    """Check if compaction is needed and perform it if so.

    Yields COMPACTION_STARTED before the LLM call and COMPACTION_ENDED after.
    Returns CompactionOutput via generator return value.
    """
    output = CompactionOutput(messages=messages)

    if not ENABLE_CONVERSATION_HISTORY_COMPACTION:
        return output

    threshold = max_context_size * get_context_window_compaction_threshold_pct() / 100
    if (initial_tokens.total_tokens + maximum_output_token) <= threshold:
        return output

    # Emit started event with pre-compaction stats
    original_stats = compute_conversation_stats(messages)
    yield StreamMessage(
        event=StreamEvents.COMPACTION_STARTED,
        data={
            "content": "Compacting conversation history...",
            "metadata": {
                "initial_tokens": initial_tokens.total_tokens,
                "max_context_size": max_context_size,
                "original_stats": original_stats.model_dump(),
            },
        },
    )

    # Run compaction
    try:
        compaction_result = compact_conversation_history(
            original_conversation_history=messages, llm=llm
        )
    except Exception as e:
        logging.error(f"Compaction failed with error: {e}", exc_info=True)
        yield StreamMessage(
            event=StreamEvents.COMPACTION_ENDED,
            data={
                "status": "error",
                "content": "Conversation compaction failed",
                "error": str(e),
                "metadata": {
                    "initial_tokens": initial_tokens.total_tokens,
                    "max_context_size": max_context_size,
                },
            },
        )
        return output

    output.usage = compaction_result.usage
    compacted_tokens = llm.count_tokens(compaction_result.messages_after_compaction, tools=tools)
    compacted_total_tokens = compacted_tokens.total_tokens

    if compacted_total_tokens >= initial_tokens.total_tokens:
        logging.debug(
            f"Failed to reduce token count when compacting conversation history. "
            f"Original tokens:{initial_tokens.total_tokens}. Compacted tokens:{compacted_total_tokens}"
        )
        yield StreamMessage(
            event=StreamEvents.COMPACTION_ENDED,
            data={
                "status": "error",
                "content": "Compaction did not reduce token count",
                "error": f"Compacted size ({compacted_total_tokens} tokens) is not smaller than original ({initial_tokens.total_tokens} tokens)",
                "metadata": {
                    "initial_tokens": initial_tokens.total_tokens,
                    "compacted_tokens": compacted_total_tokens,
                    "max_context_size": max_context_size,
                },
            },
        )
        return output

    # Success
    output.messages = compaction_result.messages_after_compaction
    output.compacted = True
    compression_ratio = round(1 - compacted_total_tokens / initial_tokens.total_tokens, 2) if initial_tokens.total_tokens > 0 else 0
    compaction_message = f"The conversation history has been compacted from {initial_tokens.total_tokens} to {compacted_total_tokens} tokens"
    logging.info(compaction_message)

    yield StreamMessage(
        event=StreamEvents.COMPACTION_ENDED,
        data={
            "status": "success",
            "content": compaction_message,
            "summary": compaction_result.summary,
            "messages": compaction_result.messages_after_compaction,
            "metadata": {
                "initial_tokens": initial_tokens.total_tokens,
                "compacted_tokens": compacted_total_tokens,
                "compression_ratio": compression_ratio,
                "max_context_size": max_context_size,
                "compaction_usage": compaction_result.usage.model_dump(),
                "original_stats": compaction_result.original_stats.model_dump(),
                "compacted_stats": compaction_result.compacted_stats.model_dump(),
            },
        },
    )
    yield StreamMessage(
        event=StreamEvents.AI_MESSAGE,
        data={"content": compaction_message},
    )
    return output


class TruncationOutput(BaseModel):
    messages: list[dict]
    metadata: dict
    tokens: TokenCountMetadata
    max_context_size: int
    maximum_output_token: int


def truncate_if_needed(
    llm: LLM, messages: list[dict], tools: Optional[list[dict[str, Any]]]
) -> TruncationOutput:
    """Truncate tool messages if conversation exceeds context window."""
    tokens = llm.count_tokens(messages=messages, tools=tools)  # type: ignore
    max_context_size = llm.get_context_window_size()
    maximum_output_token = llm.get_maximum_output_token()
    metadata: dict = {}

    if (tokens.total_tokens + maximum_output_token) > max_context_size:
        truncated_res = truncate_messages_to_fit_context(
            messages=messages,
            max_context_size=max_context_size,
            maximum_output_token=maximum_output_token,
            count_tokens_fn=llm.count_tokens,
        )
        metadata["truncations"] = [t.model_dump() for t in truncated_res.truncations]
        messages = truncated_res.truncated_messages
        tokens = llm.count_tokens(messages=messages, tools=tools)  # type: ignore
    else:
        metadata["truncations"] = []

    return TruncationOutput(
        messages=messages,
        metadata=metadata,
        tokens=tokens,
        max_context_size=max_context_size,
        maximum_output_token=maximum_output_token,
    )


class ContextWindowLimiterOutput(BaseModel):
    metadata: dict
    messages: list[dict]
    events: list[StreamMessage]
    max_context_size: int
    maximum_output_token: int
    tokens: TokenCountMetadata
    conversation_history_compacted: bool
    compaction_usage: CompactionUsage = CompactionUsage()


@sentry_sdk.trace
def limit_input_context_window(
    llm: LLM, messages: list[dict], tools: Optional[list[dict[str, Any]]]
) -> ContextWindowLimiterOutput:
    """Non-streaming path: compact + truncate, collecting all events."""
    t0 = time.monotonic()
    initial_tokens = llm.count_tokens(messages=messages, tools=tools)  # type: ignore
    max_context_size = llm.get_context_window_size()
    maximum_output_token = llm.get_maximum_output_token()

    # Compact if needed (drain generator, collect events)
    compaction_gen = compact_if_needed(llm, messages, tools, initial_tokens, max_context_size, maximum_output_token)
    events: list[StreamMessage] = []
    compaction_output = CompactionOutput(messages=messages)
    try:
        while True:
            events.append(next(compaction_gen))
    except StopIteration as e:
        compaction_output = e.value

    # Truncate if still over limit
    trunc = truncate_if_needed(llm, compaction_output.messages, tools)

    elapsed_ms = (time.monotonic() - t0) * 1000
    logging.debug(f"limit_input_context_window: {elapsed_ms:.1f}ms total | {trunc.tokens.total_tokens} tokens")

    return ContextWindowLimiterOutput(
        events=events,
        messages=trunc.messages,
        metadata=trunc.metadata,
        max_context_size=trunc.max_context_size,
        maximum_output_token=trunc.maximum_output_token,
        tokens=trunc.tokens,
        conversation_history_compacted=compaction_output.compacted,
        compaction_usage=compaction_output.usage,
    )
