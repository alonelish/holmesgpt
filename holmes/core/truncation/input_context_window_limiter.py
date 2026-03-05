import logging
from typing import Any, Optional

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
from holmes.core.truncation.compaction import CompactionUsage, compact_conversation_history
from holmes.utils.stream import StreamEvents, StreamMessage


class ContextWindowOverflowError(Exception):
    """Raised when conversation exceeds context window and cannot be compacted."""

    def __init__(self, current_tokens: int, max_tokens: int):
        self.current_tokens = current_tokens
        self.max_tokens = max_tokens

        message = (
            f"The conversation history ({current_tokens:,} tokens) exceeds the context window "
            f"({max_tokens:,} tokens) even after attempting to summarize it. "
            "This is likely a bug. Please report it at https://github.com/robusta-dev/holmesgpt/issues "
            "and start a new conversation in the meantime."
        )
        super().__init__(message)


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
    events = []
    metadata: dict = {}
    initial_tokens = llm.count_tokens(messages=messages, tools=tools)  # type: ignore
    max_context_size = llm.get_context_window_size()
    maximum_output_token = min(llm.get_maximum_output_token(), MAX_OUTPUT_TOKEN_RESERVATION)
    available_for_input = max_context_size - maximum_output_token
    conversation_history_compacted = False
    compaction_usage = CompactionUsage()

    compaction_threshold = max_context_size * get_context_window_compaction_threshold_pct() / 100

    if ENABLE_CONVERSATION_HISTORY_COMPACTION and (
        initial_tokens.total_tokens + maximum_output_token
    ) > compaction_threshold:
        compaction_result = compact_conversation_history(
            original_conversation_history=messages, llm=llm
        )
        compaction_usage = compaction_result.usage
        compacted_tokens = llm.count_tokens(compaction_result.messages_after_compaction, tools=tools)
        compacted_total_tokens = compacted_tokens.total_tokens

        if compacted_total_tokens < initial_tokens.total_tokens:
            messages = compaction_result.messages_after_compaction
            compaction_message = f"The conversation history has been compacted from {initial_tokens.total_tokens} to {compacted_total_tokens} tokens"
            logging.info(compaction_message)
            conversation_history_compacted = True
            events.append(
                StreamMessage(
                    event=StreamEvents.CONVERSATION_HISTORY_COMPACTED,
                    data={
                        "content": compaction_message,
                        "messages": compaction_result.messages_after_compaction,
                        "metadata": {
                            "initial_tokens": initial_tokens.total_tokens,
                            "compacted_tokens": compacted_total_tokens,
                        },
                    },
                )
            )
            events.append(
                StreamMessage(
                    event=StreamEvents.AI_MESSAGE,
                    data={"content": compaction_message},
                )
            )
        else:
            logging.warning(
                f"Failed to reduce token count when compacting conversation history. "
                f"Original tokens: {initial_tokens.total_tokens}. Compacted tokens: {compacted_total_tokens}"
            )

    tokens = llm.count_tokens(messages=messages, tools=tools)  # type: ignore

    if tokens.total_tokens > available_for_input:
        logging.error(
            f"Context window overflow: {tokens.total_tokens} tokens exceeds "
            f"available space of {available_for_input} tokens (max: {max_context_size}, "
            f"reserved for output: {maximum_output_token})"
        )
        raise ContextWindowOverflowError(
            current_tokens=tokens.total_tokens,
            max_tokens=available_for_input,
        )

    return ContextWindowLimiterOutput(
        events=events,
        messages=messages,
        metadata=metadata,
        max_context_size=max_context_size,
        maximum_output_token=maximum_output_token,
        tokens=tokens,
        conversation_history_compacted=conversation_history_compacted,
        compaction_usage=compaction_usage,
    )
