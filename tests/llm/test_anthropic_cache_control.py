# type: ignore
import copy
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List
from unittest.mock import patch

import litellm
import pytest

from holmes.config import Config
from holmes.core.conversations import add_or_update_system_prompt
from holmes.core.llm import DefaultLLM
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools_utils.tool_executor import ToolExecutor
from tests.llm.utils.mock_dal import load_mock_dal
from tests.llm.utils.mock_toolset import (
    MockGenerationConfig,
    MockMode,
    MockToolsetManager,
)

logger = logging.getLogger(__name__)

# Get number of iterations from env var (default 3)
NUM_ITERATIONS = int(os.environ.get("ANTHROPIC_CACHE_TEST_ITERATIONS", "5"))

# Session-scoped storage for test results (only used for aggregation in master process)
# In parallel execution, results are stored in pytest user_properties and collected at session end
_test_results: Dict[str, Dict[str, List[float]]] = defaultdict(
    lambda: {"no_cache": [], "with_cache": []}
)


def extract_cached_tokens_from_dict(usage: Dict[str, Any]) -> int:
    """Extract cached tokens from usage dict."""
    prompt_details = usage.get("prompt_tokens_details", {})
    return prompt_details.get("cached_tokens", 0)


def extract_cached_tokens_from_object(usage: Any) -> int:
    """Extract cached tokens from usage object."""
    if not hasattr(usage, "prompt_tokens_details"):
        return 0
    prompt_details = usage.prompt_tokens_details
    if not hasattr(prompt_details, "cached_tokens"):
        return 0
    return prompt_details.cached_tokens or 0


def get_cached_tokens(raw_response: Any) -> int:
    """Get cached tokens from response."""
    if not hasattr(raw_response, "usage") or not raw_response.usage:
        return 0
    usage = raw_response.usage
    if isinstance(usage, dict):
        return extract_cached_tokens_from_dict(usage)
    return extract_cached_tokens_from_object(usage)


def get_prompt_tokens(raw_response: Any) -> int:
    """Get prompt tokens from response."""
    if not hasattr(raw_response, "usage") or not raw_response.usage:
        return 0
    usage = raw_response.usage
    if isinstance(usage, dict):
        return usage.get("prompt_tokens", 0)
    return getattr(usage, "prompt_tokens", 0)


def get_short_model_name(model: str) -> str:
    """Extract short model name from full model string.

    Examples:
        "anthropic/claude-sonnet-4-20250514" -> "sonnet-4"
        "anthropic/claude-haiku-4-5-20251001" -> "haiku-4-5"
        "anthropic/claude-opus-4-5-20251101" -> "opus-4-5"
    """
    # Remove provider prefix
    if "/" in model:
        model = model.split("/", 1)[1]
    if model.startswith("claude-"):
        model = model[len("claude-") :]

    parts = model.split("-")

    # Drop trailing date if present (all digits)
    if parts and parts[-1].isdigit():
        parts = parts[:-1]

    return "-".join(parts)


def convert_thinking_blocks_for_token_counting(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert thinking blocks to text format for token counting compatibility.

    Litellm's token counter doesn't understand thinking blocks or content lists,
    so we flatten content lists to strings by extracting all text content.

    This function ensures ALL content lists are converted to strings, regardless
    of their structure, to prevent token counting errors.

    Args:
        messages: List of message dictionaries

    Returns:
        New list of messages with content lists flattened to strings
    """
    converted_messages = []
    for msg in messages:
        new_msg = copy.deepcopy(msg)
        content = new_msg.get("content")

        if content is None:
            # Handle None content (e.g., assistant messages with only tool_calls)
            new_msg["content"] = None
        elif isinstance(content, list):
            # CRITICAL: Litellm's token counter cannot handle content lists with dicts
            # We MUST convert all content lists to strings
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    # Extract text from dict blocks (thinking, text, etc.)
                    # Try common keys that contain text
                    text = (
                        item.get("text") or item.get("content") or item.get("message")
                    )
                    if text:
                        text_parts.append(str(text))
                    else:
                        # If no text found, try to extract any string value from the dict
                        for key, value in item.items():
                            if isinstance(value, str) and value:
                                text_parts.append(value)
                                break
                        # If still no text found, convert the dict to a string representation
                        # This ensures we don't lose any information
                        if not any(isinstance(v, str) and v for v in item.values()):
                            # Last resort: include a representation of the dict
                            text_parts.append(str(item))
                elif isinstance(item, str):
                    text_parts.append(item)
                else:
                    # Convert any other type to string
                    text_parts.append(str(item))
            # Join all text parts into a single string
            # Use double newline to separate blocks for readability
            new_msg["content"] = "\n\n".join(text_parts) if text_parts else ""
        elif isinstance(content, dict):
            # If content is a dict directly (unexpected format), extract text or convert
            text = content.get("text") or content.get("content")
            new_msg["content"] = str(text) if text else ""
        elif isinstance(content, str):
            # Already a string, keep as-is
            new_msg["content"] = content
        else:
            # Fallback: convert to string
            new_msg["content"] = str(content) if content else ""

        # Double-check: if content is still a list after processing, force convert to string
        # This is a safety net in case we missed something
        if isinstance(new_msg.get("content"), list):
            logger.warning(
                f"Content is still a list after conversion, forcing to string: {new_msg.get('content')}"
            )
            # Extract all text from the list and join
            text_parts = [str(item) for item in new_msg["content"] if item]
            new_msg["content"] = "\n\n".join(text_parts) if text_parts else ""

        converted_messages.append(new_msg)

    # Final validation: ensure no message has a content list
    for i, msg in enumerate(converted_messages):
        content = msg.get("content")
        if isinstance(content, list):
            logger.error(
                f"Message {i} still has list content after conversion! Role: {msg.get('role')}, Content: {content}"
            )
            # Force convert to string as last resort
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or str(item)
                    text_parts.append(str(text))
                else:
                    text_parts.append(str(item))
            converted_messages[i]["content"] = (
                "\n\n".join(text_parts) if text_parts else ""
            )

    return converted_messages


def add_uuids_to_system_prompt(
    messages: List[Dict[str, Any]], uuid_start: str, uuid_end: str
) -> List[Dict[str, Any]]:
    """Add UUIDs before and after the system prompt in messages.

    Modifies the messages list in place by adding UUIDs to the system message.
    Supports both string and list content formats.
    Also adds instruction to run TodoWrite as first tool call at the end.

    Args:
        messages: List of message dictionaries
        uuid_start: UUID string to add at the beginning of system prompt
        uuid_end: UUID string to add at the end of system prompt

    Returns:
        The modified messages list
    """
    todo_instruction = ""

    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content")
            if isinstance(content, str):
                # Add UUIDs at beginning and end, then add TodoWrite instruction at the very end
                msg["content"] = (
                    f"{uuid_start}\n\n{content}\n\n{uuid_end}\n\n{todo_instruction}"
                )
            elif isinstance(content, list):
                # Modify first and last text blocks
                if (
                    content
                    and isinstance(content[0], dict)
                    and content[0].get("type") == "text"
                ):
                    content[0]["text"] = f"{uuid_start}\n\n{content[0]['text']}"
                if (
                    content
                    and isinstance(content[-1], dict)
                    and content[-1].get("type") == "text"
                ):
                    # Add uuid_end and then TodoWrite instruction at the end
                    content[-1]["text"] = (
                        f"{content[-1]['text']}\n\n{uuid_end}\n\n{todo_instruction}"
                    )
            break
    return messages


# List of Anthropic models
ANTHROPIC_MODELS = [
    # "anthropic/claude-opus-4-5-20251101",
    # "anthropic/claude-haiku-4-5-20251001",
    "anthropic/claude-sonnet-4-5-20250929",
    # "anthropic/claude-opus-4-1-20250805",
    # "anthropic/claude-opus-4-20250514",
    # "anthropic/claude-sonnet-4-20250514",
    # "anthropic/claude-3-7-sonnet-20250219",
    # "anthropic/claude-3-5-haiku-20241022",
    # "anthropic/claude-3-haiku-20240307",
]


@pytest.fixture(scope="session", autouse=True)
def collect_test_results():
    """Fixture to collect test results and output table at end of session."""
    yield
    # Results table is output via pytest_sessionfinish hook in conftest.py


def _collect_results_from_session(session):
    """Collect test results from all workers in parallel execution."""
    # Check if we're in a worker process (xdist)
    worker_id = (
        getattr(session.config, "workerinput", {}).get("workerid", None)
        if hasattr(session.config, "workerinput")
        else None
    )

    # Only collect on master process (not in workers)
    if worker_id is not None:
        return

    # Collect results from all test items
    for item in session.items:
        if not hasattr(item, "user_properties"):
            continue

        # Group user_properties by key (multiple values per key are possible)
        props_dict = {}
        for key, value in item.user_properties:
            if isinstance(key, str) and key.startswith("anthropic_cache_"):
                if key not in props_dict:
                    props_dict[key] = []
                props_dict[key].append(value)

        # Extract model name and results from properties
        for key, values in props_dict.items():
            if key.startswith("anthropic_cache_no_cache_"):
                model = key.replace("anthropic_cache_no_cache_", "")
                _test_results[model]["no_cache"].extend(
                    [v for v in values if v is not None]
                )
            elif key.startswith("anthropic_cache_with_cache_"):
                model = key.replace("anthropic_cache_with_cache_", "")
                _test_results[model]["with_cache"].extend(
                    [v for v in values if v is not None]
                )


def _output_results_table(session=None):
    """Output a formatted table of test results."""
    import sys

    # Collect results from session if provided (for parallel execution)
    if session is not None:
        _collect_results_from_session(session)

    logger.info(
        f"Outputting results table. Collected results for {len(_test_results)} models: {list(_test_results.keys())}"
    )
    if not _test_results:
        logger.warning("No test results collected for table output")
        print("\nWARNING: No test results collected for table output", file=sys.stderr)
        return

    # Print to stderr so it's always visible (pytest captures stdout by default)
    print("\n" + "=" * 120, file=sys.stderr)
    print("ANTHROPIC CACHE CONTROL PERFORMANCE RESULTS", file=sys.stderr)
    print("=" * 120, file=sys.stderr)

    # Table header
    header = "| Model name | Runtime no cache | Runtime with cache |"
    separator = "| " + "-" * 36 + " | " + "-" * 18 + " | " + "-" * 19 + " |"

    import sys

    print(header, file=sys.stderr)
    logger.info(header)
    print(separator, file=sys.stderr)
    logger.info(separator)

    # Sort models for consistent output
    sorted_models = sorted(_test_results.keys())

    for model in sorted_models:
        results = _test_results[model]
        no_cache_times = results["no_cache"]
        with_cache_times = results["with_cache"]

        logger.debug(
            f"Model {model}: no_cache={no_cache_times}, with_cache={with_cache_times}"
        )

        # Calculate averages
        avg_no_cache = (
            sum(no_cache_times) / len(no_cache_times) if no_cache_times else 0.0
        )
        avg_with_cache = (
            sum(with_cache_times) / len(with_cache_times) if with_cache_times else 0.0
        )

        # Format row
        row = f"| {model:<36} | {avg_no_cache:>16.1f}s | {avg_with_cache:>17.1f}s |"
        print(row, file=sys.stderr)
        logger.info(row)

    footer = "=" * 120 + "\n"
    print(footer, file=sys.stderr)
    logger.info(footer)


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize("model", ANTHROPIC_MODELS)
@pytest.mark.parametrize("iteration", range(NUM_ITERATIONS))
def test_anthropic_cache_control_chat(request, model, iteration):
    """Test that Anthropic models send cache_control and receive cached tokens on second call.

    Runs multiple iterations (controlled by ANTHROPIC_CACHE_TEST_ITERATIONS env var, default 3)
    to measure performance consistency.
    """
    # Get API key from environment
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # Skip if no API key
    if not anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY env var not set")

    # Get short model name for logging (needed early for logging)
    model_name = get_short_model_name(model)

    # Set required environment variables for thinking and temperature if not already set
    # These need to be set before creating DefaultLLM instance
    # User can set them via command line: THINKING='{"type": "enabled", "budget_tokens": 10000}' TEMPERATURE=1 pytest ...
    if "THINKING" not in os.environ:
        os.environ["THINKING"] = '{"type": "enabled", "budget_tokens": 10000}'
    if "TEMPERATURE" not in os.environ:
        os.environ["TEMPERATURE"] = "1"

    # Reload env_vars and llm modules to pick up the new environment variables
    # We need to reload both because llm imports THINKING from env_vars at module import time
    import importlib

    from holmes.common import env_vars
    from holmes.core import llm

    importlib.reload(env_vars)
    importlib.reload(llm)
    # Also reload input_context_window_limiter to ensure it uses the same TokenCountMetadata class
    from holmes.core.truncation import input_context_window_limiter

    importlib.reload(input_context_window_limiter)

    # Verify THINKING is set correctly
    logger.info(f"{model_name} test THINKING env var: {os.environ.get('THINKING')}")
    logger.info(
        f"{model_name} test TEMPERATURE env var: {os.environ.get('TEMPERATURE')}"
    )

    # Validate environment
    env_check = litellm.validate_environment(model=model)
    if not env_check["keys_in_environment"]:
        pytest.skip(
            f"Missing API keys for model {model}. Required: {', '.join(env_check['missing_keys'])}"
        )

    # Generate UUIDs for this test (same UUIDs for both iterations to test cache)
    uuid_start = str(uuid.uuid4())
    uuid_end = str(uuid.uuid4())
    logger.info(f"{model_name} test using UUIDs: start={uuid_start}, end={uuid_end}")

    # Track completion calls to verify cache_control and measure LLM call times
    completion_calls: List[Dict[str, Any]] = []
    raw_responses: List[Any] = []
    llm_call_times: List[float] = []
    original_litellm_completion = litellm.completion

    def capture_litellm_completion(*args, **kwargs):
        """Capture completion calls to verify cache_control and measure LLM API time."""
        # Don't filter tools - let all tools through for accurate speed testing
        messages = kwargs.get("messages", [])
        tools = kwargs.get("tools", [])

        # Verify the last non-user message has cache_control when thinking is enabled
        if os.environ.get("THINKING"):
            # Find the last non-user message (should be system or assistant)
            for msg in reversed(messages):
                if msg.get("role") != "user":
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        # Check if any content block has cache_control
                        has_cache_control = any(
                            isinstance(block, dict) and "cache_control" in block
                            for block in content
                        )
                        if not has_cache_control:
                            logger.warning(
                                f"{model_name} test: Last non-user message (role={msg.get('role')}) "
                                f"does not have cache_control in content blocks"
                            )
                    break

        # Store the call arguments for verification
        call_info = {
            "messages": messages,
            "tools": tools,
        }
        completion_calls.append(call_info)

        # Measure LLM API call time (from start to completion finish)
        llm_start_time = time.time()
        result = original_litellm_completion(*args, **kwargs)
        llm_call_time = time.time() - llm_start_time

        llm_call_times.append(llm_call_time)
        raw_responses.append(result)
        return result

    with patch.object(litellm, "completion", side_effect=capture_litellm_completion):
        llm = DefaultLLM(model, tracer=None)
        mock_generation_config = MockGenerationConfig(
            generate_mocks_enabled=False,
            regenerate_all_enabled=False,
            mock_mode=MockMode.MOCK,
        )

        temp_dir = TemporaryDirectory()
        try:
            toolset_manager = MockToolsetManager(
                test_case_folder=str(temp_dir.name),
                mock_generation_config=mock_generation_config,
                request=request,
            )
            tool_executor = ToolExecutor(toolset_manager.toolsets)

            # No tool filtering - allow all tools for accurate speed testing

            # Log enabled toolsets
            enabled_toolsets = [ts.name for ts in tool_executor.enabled_toolsets]
            expected_toolsets = [
                "kubernetes/core",
                "kubernetes/logs",
                "robusta",
                "internet",
                "runbook",
            ]
            missing_toolsets = [
                ts for ts in expected_toolsets if ts not in enabled_toolsets
            ]
            logger.info(
                f"{model_name} test enabled toolsets ({len(enabled_toolsets)}): {', '.join(enabled_toolsets)}"
            )
            if missing_toolsets:
                logger.warning(
                    f"{model_name} test missing expected toolsets: {', '.join(missing_toolsets)}"
                )
            else:
                logger.info(f"{model_name} test all expected toolsets present")

            # Allow multiple steps for full tool execution (no artificial limits for speed testing)
            ai = ToolCallingLLM(tool_executor=tool_executor, max_steps=10, llm=llm)
            config = Config()

            mock_dal = load_mock_dal(
                Path(temp_dir.name), generate_mocks=False, initialize_base=False
            )
            runbooks = config.get_runbook_catalog()

            # Load conversation history from JSON file
            conversation_history_path = (
                Path(__file__).parent.parent
                / "llm"
                / "fixtures"
                / "compaction"
                / "008_very_long_conversation"
                / "conversation_history.json"
            )
            with open(conversation_history_path, "r") as f:
                messages = json.load(f)

            # When thinking is enabled, ensure the conversation doesn't end with an assistant message
            # Anthropic requires that when thinking is enabled, if there's a final assistant message,
            # it must start with a thinking block. To avoid this requirement, we ensure the
            # conversation ends with a user message instead.
            if (
                os.environ.get("THINKING")
                and messages
                and messages[-1].get("role") == "assistant"
            ):
                # Remove the last assistant message to avoid the thinking block requirement
                # We'll add a user message instead
                messages = messages[:-1]
                # Add a user message at the end
                messages.append({"role": "user", "content": "What pods are crashing?"})

            # Replace the system prompt with the one from build_chat_messages
            # This ensures we use the same system prompt format as the actual chat flow
            messages = add_or_update_system_prompt(
                conversation_history=messages,
                ai=ai,
                config=config,
                additional_system_prompt=None,
                runbooks=runbooks,
            )

            # Add UUIDs before and after system prompt
            messages = add_uuids_to_system_prompt(messages, uuid_start, uuid_end)

            # Store original count_tokens method
            original_count_tokens = llm.count_tokens

            # Override count_tokens to convert thinking blocks only for token counting
            # This allows us to keep thinking blocks in messages for the API call
            # but convert them for token counting (which litellm doesn't support)
            def count_tokens_with_conversion(
                messages_for_counting=None, tools=None, **kwargs
            ):
                # Handle both positional and keyword arguments
                if messages_for_counting is None and "messages" in kwargs:
                    messages_for_counting = kwargs["messages"]
                if tools is None and "tools" in kwargs:
                    tools = kwargs.get("tools")
                converted_messages = convert_thinking_blocks_for_token_counting(
                    messages_for_counting
                )
                # Call original method - it returns TokenCountMetadata which is compatible
                return original_count_tokens(messages=converted_messages, tools=tools)

            llm.count_tokens = count_tokens_with_conversion

            # Add assistant message with TodoWrite tool call at the end
            import uuid as uuid_module

            tool_call_id = f"call_{uuid_module.uuid4().hex[:12]}"

            # Count tokens in the messages we're sending (will use converted version)
            try:
                token_count = llm.count_tokens(messages=messages, tools=None)
                logger.info(
                    f"{model_name} test messages token count: {token_count.total_tokens} total tokens "
                    f"({token_count.system_tokens} system, {token_count.user_tokens} user)"
                )
            except (ValueError, Exception) as e:
                logger.warning(f"{model_name} test could not count tokens: {e}")

            responses_per_iteration: List[
                List[Any]
            ] = []  # Track responses per iteration
            llm_times_per_iteration: List[
                List[float]
            ] = []  # Track LLM times per iteration

            no_cache_runtime = None
            with_cache_runtime = None

            for call_idx in range(2):
                # Track starting indices for this iteration
                start_response_count = len(raw_responses)
                start_llm_time_count = len(llm_call_times)

                call_start_time = time.time()

                # Create a deep copy of messages for each iteration
                # messages_call modifies messages in place (adds tool calls/responses),
                # so we need a fresh copy for each iteration to keep them identical
                messages_copy = copy.deepcopy(messages)
                result = ai.messages_call(messages=messages_copy, trace_span=None)
                call_duration = time.time() - call_start_time

                assert result is not None

                # Get responses and LLM times for this iteration
                iteration_responses = raw_responses[start_response_count:]
                iteration_llm_times = llm_call_times[start_llm_time_count:]
                responses_per_iteration.append(iteration_responses)
                llm_times_per_iteration.append(iteration_llm_times)

                # Sum LLM API times for this iteration (may have multiple calls)
                total_llm_api_time = (
                    sum(iteration_llm_times) if iteration_llm_times else 0.0
                )

                # Store runtime for table output
                if call_idx == 0:
                    no_cache_runtime = call_duration
                else:
                    with_cache_runtime = call_duration

                # Print tool call times with model name and test type
                test_type = "not in cache" if call_idx == 0 else "in cache"
                iteration_label = (
                    f"[iter {iteration + 1}/{NUM_ITERATIONS}]"
                    if NUM_ITERATIONS > 1
                    else ""
                )

                # Log total duration and LLM API time
                if result.tool_calls:
                    tool_execution_time = call_duration - total_llm_api_time
                    logger.info(
                        f"{model_name} test {test_type} {iteration_label} total {call_duration:.1f}s "
                        f"(LLM API: {total_llm_api_time:.1f}s, tools: {tool_execution_time:.1f}s, "
                        f"tool calls: {len(result.tool_calls)})"
                    )
                    for i, tool_call in enumerate(result.tool_calls, 1):
                        logger.info(
                            f"  Tool call {i}: {tool_call.tool_name} - {tool_call.description}"
                        )
                else:
                    logger.info(
                        f"{model_name} test {test_type} {iteration_label} total {call_duration:.1f}s "
                        f"(LLM API: {total_llm_api_time:.1f}s)"
                    )

            if no_cache_runtime is not None:
                # Store in user_properties (works with xdist)
                # user_properties is a list of tuples, so we append a new tuple
                request.node.user_properties.append(
                    (f"anthropic_cache_no_cache_{model}", no_cache_runtime)
                )
                # Also store in local dict for non-parallel execution
                _test_results[model]["no_cache"].append(no_cache_runtime)
                logger.debug(
                    f"Stored no_cache runtime {no_cache_runtime:.1f}s for {model}"
                )

            if with_cache_runtime is not None:
                request.node.user_properties.append(
                    (f"anthropic_cache_with_cache_{model}", with_cache_runtime)
                )
                # Also store in local dict for non-parallel execution
                _test_results[model]["with_cache"].append(with_cache_runtime)
                logger.debug(
                    f"Stored with_cache runtime {with_cache_runtime:.1f}s for {model}"
                )

            # Verify we made at least 2 completion calls
            assert len(completion_calls) >= 2, "Expected at least 2 completion calls"

            # Get total number of tools available from tool executor
            all_tools = tool_executor.get_all_tools_openai_format(target_model=model)
            total_available_tools = len(all_tools)
            tool_names_available = [
                tool.get("function", {}).get("name", "unknown") for tool in all_tools
            ]

            logger.info(
                f"{model_name} test total tools available: {total_available_tools} "
                f"(from {len(enabled_toolsets)} enabled toolsets)"
            )

            # Log all completion calls to see when tools are included
            logger.info(
                f"{model_name} test total completion calls: {len(completion_calls)}"
            )

            # Log the messages being sent to understand token counts
            # Check all calls, not just first 2, to see when tools are included
            for i, call in enumerate(completion_calls):
                call_messages = call.get("messages", [])
                call_tools = call.get("tools", [])

                # Extract tool names for logging
                tool_names = []
                if call_tools:
                    for tool in call_tools:
                        if isinstance(tool, dict):
                            tool_name = tool.get("function", {}).get("name", "unknown")
                            if tool_name:
                                tool_names.append(tool_name)
                        elif hasattr(tool, "name"):
                            tool_names.append(tool.name)

                # Verify cache_control on last non-user message (for speed testing)
                if i > 0:  # Check cache on second call onwards
                    last_non_user_msg = None
                    for msg in reversed(call_messages):
                        if msg.get("role") != "user":
                            last_non_user_msg = msg
                            break

                    if last_non_user_msg:
                        content = last_non_user_msg.get("content", [])
                        if isinstance(content, list):
                            # Check if system message has cache_control with TTL
                            has_cache_with_ttl = False
                            for block in content:
                                if isinstance(block, dict):
                                    cache_control = block.get("cache_control")
                                    if cache_control and isinstance(
                                        cache_control, dict
                                    ):
                                        if cache_control.get("ttl"):
                                            has_cache_with_ttl = True
                                            logger.info(
                                                f"{model_name} test call {i+1}: Last non-user message has "
                                                f"cache_control with TTL: {cache_control}"
                                            )
                                            break

                            if (
                                not has_cache_with_ttl
                                and last_non_user_msg.get("role") == "system"
                            ):
                                logger.warning(
                                    f"{model_name} test call {i+1}: System message does not have "
                                    f"cache_control with TTL"
                                )

                # Count tokens in the messages using the LLM's token counter (only for first 2 calls to avoid slowdown)
                if call_messages and i < 2:
                    call_token_count = llm.count_tokens(
                        messages=call_messages, tools=call_tools
                    )
                    system_msg = next(
                        (m for m in call_messages if m.get("role") == "system"), None
                    )
                    if system_msg:
                        content = system_msg.get("content", "")
                        if isinstance(content, str):
                            system_chars = len(content)
                        else:
                            system_chars = len(str(content))

                        # Determine which iteration this call belongs to
                        iteration_num = (
                            (i // 2) + 1 if i < len(completion_calls) else "?"
                        )
                        call_in_iteration = (i % 2) + 1

                        logger.info(
                            f"{model_name} test call {i+1} (iter {iteration_num}, call {call_in_iteration}): "
                            f"{len(call_messages)} messages, "
                            f"{len(call_tools)} tools sent as separate parameter, "
                            f"{total_available_tools} tools available total, "
                            f"token count: {call_token_count.total_tokens} total "
                            f"({call_token_count.system_tokens} system, {call_token_count.user_tokens} user, "
                            f"{call_token_count.tools_to_call_tokens} tools_to_call), "
                            f"system prompt {system_chars} chars"
                        )
                        if tool_names:
                            logger.info(
                                f"{model_name} test call {i+1} tools in call ({len(tool_names)}): "
                                f"{', '.join(tool_names[:10])}"
                                + (
                                    f" ... and {len(tool_names) - 10} more"
                                    if len(tool_names) > 10
                                    else ""
                                )
                            )
                        elif len(call_tools) == 0:
                            logger.info(
                                f"{model_name} test call {i+1} tools are embedded in system prompt "
                                f"(not passed as separate parameter). Total tools available: {total_available_tools}"
                            )
                            # Show first few tool names
                            if tool_names_available:
                                logger.info(
                                    f"{model_name} test available tools (first 10): "
                                    f"{', '.join(tool_names_available[:10])}"
                                    + (
                                        f" ... and {len(tool_names_available) - 10} more"
                                        if len(tool_names_available) > 10
                                        else ""
                                    )
                                )

            # Verify we have responses for both iterations
            assert (
                len(responses_per_iteration) >= 2
            ), "Expected responses for 2 iterations"
            assert (
                len(responses_per_iteration[0]) > 0
            ), "Expected at least one response in first iteration"
            assert (
                len(responses_per_iteration[1]) > 0
            ), "Expected at least one response in second iteration"

            # Log how many LLM calls were made in each iteration
            logger.info(
                f"{model_name} test first iteration: {len(responses_per_iteration[0])} LLM calls, "
                f"second iteration: {len(responses_per_iteration[1])} LLM calls"
            )

            # Get the LAST LLM call's response from each iteration (this should include full system prompt with tools)
            # The first call might be smaller, but the last call should have the full context
            first_response = responses_per_iteration[0][-1]
            second_response = responses_per_iteration[1][-1]

            # Also log the first call for comparison
            if len(responses_per_iteration[0]) > 1:
                first_response_first_call = responses_per_iteration[0][0]
                first_prompt_tokens_first_call = get_prompt_tokens(
                    first_response_first_call
                )
                logger.info(
                    f"{model_name} test first iteration first call: {first_prompt_tokens_first_call} prompt tokens"
                )

            first_cached_tokens = get_cached_tokens(first_response)
            second_cached_tokens = get_cached_tokens(second_response)
            first_prompt_tokens = get_prompt_tokens(first_response)
            second_prompt_tokens = get_prompt_tokens(second_response)

            # Log full usage details to understand the discrepancy
            if hasattr(first_response, "usage") and first_response.usage:
                usage = first_response.usage
                if isinstance(usage, dict):
                    logger.info(f"{model_name} test first call usage: {usage}")
                else:
                    logger.info(
                        f"{model_name} test first call usage: prompt_tokens={getattr(usage, 'prompt_tokens', 'N/A')}, "
                        f"total_tokens={getattr(usage, 'total_tokens', 'N/A')}, "
                        f"prompt_tokens_details={getattr(usage, 'prompt_tokens_details', 'N/A')}"
                    )

            # Also log second call usage for comparison
            if hasattr(second_response, "usage") and second_response.usage:
                usage = second_response.usage
                if isinstance(usage, dict):
                    logger.info(f"{model_name} test second call usage: {usage}")
                else:
                    logger.info(
                        f"{model_name} test second call usage: prompt_tokens={getattr(usage, 'prompt_tokens', 'N/A')}, "
                        f"total_tokens={getattr(usage, 'total_tokens', 'N/A')}, "
                        f"prompt_tokens_details={getattr(usage, 'prompt_tokens_details', 'N/A')}"
                    )

            logger.info(
                f"{model_name} test first call: {first_cached_tokens} cached tokens, {first_prompt_tokens} prompt tokens"
            )
            logger.info(
                f"{model_name} test second call: {second_cached_tokens} cached tokens, {second_prompt_tokens} prompt tokens"
            )

            # Note: First call may have cached tokens from previous test runs (cache persists across runs)
            # The important thing is that the second call uses the cache from the first call
            # Second call should have cached tokens (most of the prompt should be cached)
            if second_cached_tokens > 0:
                cache_ratio = (
                    (second_cached_tokens / second_prompt_tokens * 100)
                    if second_prompt_tokens > 0
                    else 0
                )
                logger.info(
                    f"{model_name} test cache ratio: {cache_ratio:.1f}% ({second_cached_tokens}/{second_prompt_tokens})"
                )
            else:
                logger.warning(
                    f"{model_name} test second call has no cached tokens - cache may not be working"
                )

            # For the system prompt to be fully cached, cached tokens should be >= first prompt tokens
            # This ensures the entire first call (system + user) is cached
            assert (
                second_cached_tokens >= first_prompt_tokens * 0.95
            ), f"Expected cached tokens ({second_cached_tokens}) >= 95% of first prompt tokens ({first_prompt_tokens}) to ensure system prompt is fully cached. Cache ratio: {cache_ratio:.1%}"

        finally:
            temp_dir.cleanup()
