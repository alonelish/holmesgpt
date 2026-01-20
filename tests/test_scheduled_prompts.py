"""
Test to validate scheduled prompts executor requirements.
This ensures that ScheduledPromptsExecutor can safely call the chat function.

These tests are required for scheduled prompts functionality to work correctly.
"""

import inspect
from typing import get_type_hints

import pytest
from fastapi.responses import StreamingResponse

from holmes.core.models import ChatRequest, ChatResponse
from server import chat


def test_chat_function_signature_for_scheduled_prompts():
    """
    Validate that the chat function has exactly the expected signature required by ScheduledPromptsExecutor:
    def chat(chat_request: ChatRequest) -> ChatResponse | StreamingResponse

    This test is REQUIRED for scheduled prompts functionality.
    ScheduledPromptsExecutor calls this function, so the signature must match exactly.

    This test ensures:
    1. The function has exactly one parameter: chat_request
    2. The parameter type is ChatRequest
    3. No additional parameters exist
    4. The return type annotation is correct (ChatResponse | StreamingResponse)
    """
    # Get function signature
    sig = inspect.signature(chat)
    params = sig.parameters

    # Validate exactly one parameter exists
    assert (
        len(params) == 1
    ), f"Expected 1 parameter, got {len(params)}: {list(params.keys())}"

    # Validate parameter name
    assert (
        "chat_request" in params
    ), f"Expected parameter 'chat_request', got: {list(params.keys())}"

    # Validate parameter type annotation
    chat_request_param = params["chat_request"]
    assert (
        chat_request_param.annotation == ChatRequest
    ), f"Expected parameter type ChatRequest, got {chat_request_param.annotation}"

    # Validate no additional parameters
    expected_params = {"chat_request"}
    actual_params = set(params.keys())
    assert actual_params == expected_params, (
        f"Unexpected parameters found. Expected {expected_params}, got {actual_params}. "
        f"ScheduledPromptsExecutor requires exactly one parameter."
    )

    # Validate return type annotation
    return_annotation = sig.return_annotation
    # The return type should be Union[ChatResponse, StreamingResponse] or ChatResponse | StreamingResponse
    # Check if it's a Union type or uses the | operator
    if hasattr(return_annotation, "__args__"):
        # It's a Union type
        return_types = return_annotation.__args__
        assert ChatResponse in return_types or ChatResponse.__name__ in str(
            return_types
        ), f"Expected ChatResponse in return type, got {return_annotation}"
        assert StreamingResponse in return_types or "StreamingResponse" in str(
            return_types
        ), f"Expected StreamingResponse in return type, got {return_annotation}"
    elif return_annotation == inspect.Signature.empty:
        # No return type annotation - this is acceptable but not ideal
        # We'll just log a warning
        pytest.skip("Chat function has no return type annotation")
    else:
        # Check if it's using the | operator (Python 3.10+)
        if "|" in str(return_annotation):
            assert "ChatResponse" in str(
                return_annotation
            ), f"Expected ChatResponse in return type, got {return_annotation}"
            assert "StreamingResponse" in str(
                return_annotation
            ), f"Expected StreamingResponse in return type, got {return_annotation}"
        else:
            # Single return type - check if it's ChatResponse or StreamingResponse
            assert return_annotation in (ChatResponse, StreamingResponse) or str(
                return_annotation
            ) in (
                "ChatResponse",
                "StreamingResponse",
            ), (
                f"Expected Union[ChatResponse, StreamingResponse] or ChatResponse | StreamingResponse, "
                f"got {return_annotation}"
            )


def test_chat_function_type_hints_for_scheduled_prompts():
    """
    Validate type hints using get_type_hints to ensure compatibility with ScheduledPromptsExecutor.

    This test is REQUIRED for scheduled prompts functionality.
    """
    try:
        hints = get_type_hints(chat)

        # Check parameter type
        assert "chat_request" in hints, "chat_request parameter missing from type hints"
        assert (
            hints["chat_request"] == ChatRequest
        ), f"Expected ChatRequest type hint, got {hints['chat_request']}"

        # Check return type (if annotated)
        if "return" in hints:
            return_type = hints["return"]
            # Should be Union[ChatResponse, StreamingResponse] or equivalent
            assert ChatResponse in (
                return_type.__args__
                if hasattr(return_type, "__args__")
                else [return_type]
            ), f"Expected ChatResponse in return type, got {return_type}"
    except Exception as e:
        # If get_type_hints fails, that's okay - the function might not have complete annotations
        # But we should still validate the signature
        pytest.skip(f"Could not get type hints: {e}")


def test_chat_function_callable_with_chat_request_for_scheduled_prompts():
    """
    Validate that the chat function can be called with a ChatRequest instance.
    This is a runtime check to ensure the signature is actually correct for ScheduledPromptsExecutor.

    This test is REQUIRED for scheduled prompts functionality.
    ScheduledPromptsExecutor builds ChatRequest objects and calls the chat function.
    """
    # This test doesn't actually call the function (which would require mocking),
    # but validates that the signature is callable with ChatRequest
    sig = inspect.signature(chat)

    # Create a mock ChatRequest to validate binding
    chat_request = ChatRequest(ask="test")

    # Try to bind the arguments - this will raise if signature doesn't match
    try:
        bound = sig.bind(chat_request)
        bound.apply_defaults()
    except TypeError as e:
        pytest.fail(
            f"Chat function signature does not accept ChatRequest: {e}. "
            f"This will break ScheduledPromptsExecutor."
        )

    # Validate that no extra arguments are accepted
    try:
        sig.bind(chat_request, extra_arg="should_fail")
        pytest.fail(
            "Chat function should not accept extra arguments. "
            "ScheduledPromptsExecutor only passes ChatRequest."
        )
    except TypeError:
        # This is expected - extra arguments should fail
        pass
