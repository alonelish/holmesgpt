import logging
from typing import Optional

import litellm
from litellm.types.utils import ModelResponse

from holmes.core.llm import LLM
from holmes.plugins.prompts import load_and_render_prompt


def strip_system_prompt(
    conversation_history: list[dict],
) -> tuple[list[dict], Optional[dict]]:
    if not conversation_history:
        return [], None  # Return new empty list to avoid mutation
    first_message = conversation_history[0]
    if first_message and first_message.get("role") == "system":
        return conversation_history[1:], first_message
    return conversation_history[:], None


def find_last_user_prompt(conversation_history: list[dict]) -> Optional[dict]:
    if not conversation_history:
        return None
    last_user_prompt: Optional[dict] = None
    for message in conversation_history:
        if message.get("role") == "user":
            last_user_prompt = message
    return last_user_prompt


def extract_conversation_without_tool_calls(
    conversation_history: list[dict],
) -> list[dict]:
    """
    Extract only user and assistant messages, stripping tool calls and tool results.

    This preserves the conversational flow without the bulk of tool call data.
    """
    conversation_messages = []
    for message in conversation_history:
        role = message.get("role")

        if role == "user":
            # Keep all user messages
            conversation_messages.append(message)
        elif role == "assistant":
            # Keep assistant messages, but strip tool_calls
            if not message.get("tool_calls"):
                # Assistant text response without tool calls - keep it
                conversation_messages.append(message)
            # Skip assistant messages that only contain tool_calls
        # Skip tool result messages entirely

    return conversation_messages


def compact_conversation_history(
    original_conversation_history: list[dict], llm: LLM
) -> list[dict]:
    """
    Smart compaction that preserves conversational flow when possible.

    Strategy:
      1. Generate LLM summary of the full conversation (includes tool call context)
      2. Start with base: [system prompt, summary, continue message]
      3. Try to add back actual user/assistant messages (without tool calls/results)
      4. Only add them if they fit within context budget

    The compacted conversation history contains:
      1. Original system prompt (if present)
      2. LLM-generated summary of entire conversation (role=assistant)
      3. Compaction continuation message (role=system)
      4. Original conversation messages without tool calls (if they fit)
    """
    conversation_history, system_prompt_message = strip_system_prompt(
        original_conversation_history
    )
    compaction_instructions = load_and_render_prompt(
        prompt="builtin://conversation_history_compaction.jinja2", context={}
    )
    conversation_history.append({"role": "user", "content": compaction_instructions})

    # Set modify_params to handle providers like Anthropic that require tools
    # when conversation history contains tool calls
    original_modify_params = litellm.modify_params
    try:
        litellm.modify_params = True  # necessary when using anthropic
        response: ModelResponse = llm.completion(
            messages=conversation_history, drop_params=True
        )  # type: ignore
    finally:
        litellm.modify_params = original_modify_params
    response_message = None
    if (
        response
        and response.choices
        and response.choices[0]
        and response.choices[0].message  # type:ignore
    ):
        response_message = response.choices[0].message  # type:ignore
    else:
        logging.error(
            "Failed to compact conversation history. Unexpected LLM's response for compaction"
        )
        return original_conversation_history

    # Build base compacted history: system + summary + continue
    compacted_conversation_history: list[dict] = []
    if system_prompt_message:
        compacted_conversation_history.append(system_prompt_message)

    summary_message = response_message.model_dump(
        exclude_defaults=True, exclude_unset=True, exclude_none=True
    )
    compacted_conversation_history.append(summary_message)

    continue_message = {
        "role": "system",
        "content": "The conversation history has been compacted to preserve available space in the context window. Continue.",
    }
    compacted_conversation_history.append(continue_message)

    # Try to add back the actual conversation (without tool calls) if it fits
    conversation_without_tools = extract_conversation_without_tool_calls(
        original_conversation_history
    )

    if conversation_without_tools:
        # Calculate token budget
        # Use reasonable estimates: assume we want to leave room for output
        max_context_size = llm.get_context_window_size()
        maximum_output_token = llm.get_maximum_output_token()
        target_budget = max_context_size - maximum_output_token

        # Check if adding conversation messages fits in budget
        test_history = compacted_conversation_history + conversation_without_tools
        test_tokens = llm.count_tokens(messages=test_history)

        if test_tokens.total_tokens <= target_budget:
            # It fits! Add the conversation messages
            compacted_conversation_history.extend(conversation_without_tools)
            logging.info(
                f"Compaction: preserved {len(conversation_without_tools)} conversation messages "
                f"({test_tokens.total_tokens} total tokens)"
            )
        else:
            logging.info(
                f"Compaction: conversation messages don't fit in budget "
                f"({test_tokens.total_tokens} tokens > {target_budget} target), "
                f"using summary only"
            )

    return compacted_conversation_history
