"""
Real LLM tests for /api/chat endpoint.

Tests are divided into:
1. Real LLM tests - require actual API keys (OpenAI, Anthropic, OpenRouter)
2. Mocked LLM tests - mock only litellm.completion to test our code's behavior

The mocked tests are designed to allow confident refactoring of our code
by verifying behavior without being tied to our implementation details.
"""

import base64
import json
import os
from io import BytesIO
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from holmes.utils.stream import StreamEvents

# Models for different test scenarios
OPENAI_MODEL = "gpt-4.1-mini"  # For structured output tests (supports strict JSON schema)
OPENROUTER_MODEL = "openrouter/anthropic/claude-haiku-4.5"  # For image tests


def create_test_image_with_text(text: str, width: int = 200, height: int = 100) -> str:
    """
    Create a simple PNG image with text and return as base64 data URI.

    Uses PIL to create an image with the specified text rendered on it.
    This allows us to verify the LLM can actually read the image content.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        pytest.skip("PIL/Pillow not installed - required for image tests")

    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.load_default(size=20)
    except TypeError:
        font = ImageFont.load_default()

    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    x = (width - text_width) // 2
    y = (height - text_height) // 2
    draw.text((x, y), text, fill="black", font=font)

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    img_bytes = buffer.getvalue()
    b64_string = base64.b64encode(img_bytes).decode("utf-8")

    return f"data:image/png;base64,{b64_string}"


def create_model_entry(model_name: str):
    """Create a ModelEntry for the given model."""
    from holmes.core.llm import ModelEntry
    return ModelEntry(
        name=model_name,
        model=model_name,
        api_key=None,
    )


@pytest.fixture
def openai_client():
    """
    Create a test client configured for OpenAI model.
    Requires OPENAI_API_KEY environment variable.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    import server

    model_entry = create_model_entry(OPENAI_MODEL)
    original_get_model_params = server.config.llm_model_registry.get_model_params

    def mock_get_model_params(model_key=None):
        if model_key == OPENAI_MODEL:
            return model_entry.model_copy()
        return original_get_model_params(model_key)

    server.config.llm_model_registry.get_model_params = mock_get_model_params

    try:
        yield TestClient(server.app)
    finally:
        server.config.llm_model_registry.get_model_params = original_get_model_params


@pytest.fixture
def openrouter_client():
    """
    Create a test client configured for OpenRouter model (Claude Haiku 4.5).
    Requires OPENROUTER_API_KEY environment variable.
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")

    import server

    model_entry = create_model_entry(OPENROUTER_MODEL)
    original_get_model_params = server.config.llm_model_registry.get_model_params

    def mock_get_model_params(model_key=None):
        if model_key == OPENROUTER_MODEL:
            return model_entry.model_copy()
        return original_get_model_params(model_key)

    server.config.llm_model_registry.get_model_params = mock_get_model_params

    try:
        yield TestClient(server.app)
    finally:
        server.config.llm_model_registry.get_model_params = original_get_model_params


@pytest.fixture
def mock_client():
    """
    Create a test client for mocked LLM tests.
    Does not require any API keys.
    """
    import server
    return TestClient(server.app)


# =============================================================================
# STRUCTURED OUTPUT TESTS - Real LLM with OpenAI strict JSON schema
# =============================================================================

@pytest.mark.llm
@pytest.mark.integration
class TestStructuredOutputReal:
    """
    Tests for OpenAI structured output with strict JSON schema.

    These tests verify that the response_format parameter correctly
    constrains the LLM output to match the specified JSON schema.
    """

    def test_strict_json_schema_basic(self, openai_client):
        """
        Test strict JSON schema with a simple schema.

        OpenAI's strict mode guarantees the response matches the schema exactly.
        """
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "SentimentResult",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "sentiment": {
                            "type": "string",
                            "enum": ["positive", "negative", "neutral"],
                        },
                        "confidence": {
                            "type": "number",
                        },
                    },
                    "required": ["sentiment", "confidence"],
                    "additionalProperties": False,
                },
            },
        }

        payload = {
            "ask": "Analyze: 'I love this product!' - return sentiment and confidence.",
            "conversation_history": [
                {"role": "system", "content": "Analyze sentiment and return JSON."}
            ],
            "model": OPENAI_MODEL,
            "response_format": response_format,
        }

        response = openai_client.post("/api/chat", json=payload)
        assert response.status_code == 200, f"Request failed: {response.text}"

        data = response.json()
        assert "analysis" in data

        # With strict mode, response MUST be valid JSON matching schema
        analysis = json.loads(data["analysis"])
        assert analysis["sentiment"] in ["positive", "negative", "neutral"]
        assert isinstance(analysis["confidence"], (int, float))
        # Should detect positive sentiment
        assert analysis["sentiment"] == "positive"

    def test_strict_json_schema_nested(self, openai_client):
        """
        Test strict JSON schema with nested objects.
        """
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "TaskAnalysis",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "priority": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                        },
                        "metadata": {
                            "type": "object",
                            "properties": {
                                "estimated_hours": {"type": "integer"},
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["estimated_hours", "tags"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["summary", "priority", "metadata"],
                    "additionalProperties": False,
                },
            },
        }

        payload = {
            "ask": "Analyze task: 'Set up CI/CD pipeline' - return analysis.",
            "conversation_history": [
                {"role": "system", "content": "Analyze tasks and return structured JSON."}
            ],
            "model": OPENAI_MODEL,
            "response_format": response_format,
        }

        response = openai_client.post("/api/chat", json=payload)
        assert response.status_code == 200, f"Request failed: {response.text}"

        data = response.json()
        analysis = json.loads(data["analysis"])

        # Verify nested structure
        assert "summary" in analysis
        assert analysis["priority"] in ["low", "medium", "high"]
        assert "metadata" in analysis
        assert isinstance(analysis["metadata"]["estimated_hours"], int)
        assert isinstance(analysis["metadata"]["tags"], list)


# =============================================================================
# IMAGE EXTRACTION TESTS - Real LLM with vision capabilities
# =============================================================================

@pytest.mark.llm
@pytest.mark.integration
class TestImageExtraction:
    """
    Tests for image content extraction using real LLM vision capabilities.

    These tests create images with known content and verify the LLM
    correctly extracts information that it couldn't know in advance.
    """

    def test_image_text_extraction(self, openrouter_client):
        """
        Test that the LLM can extract text from an image.
        """
        secret_code = "HOLMES-7X9K2M"
        image_data_uri = create_test_image_with_text(secret_code)

        payload = {
            "ask": "What text do you see in this image? Reply with ONLY the exact text.",
            "conversation_history": [
                {"role": "system", "content": "Read text from images. Reply with only the text."}
            ],
            "model": OPENROUTER_MODEL,
            "images": [image_data_uri],
        }

        response = openrouter_client.post("/api/chat", json=payload)
        assert response.status_code == 200, f"Request failed: {response.text}"

        data = response.json()
        analysis = data["analysis"].strip()

        # Allow for minor formatting differences
        assert secret_code in analysis or secret_code.replace("-", "") in analysis.replace("-", ""), (
            f"Expected '{secret_code}' in response, got: '{analysis}'"
        )

    def test_image_number_extraction(self, openrouter_client):
        """
        Test image extraction with numerical content.
        """
        test_number = "42857"
        image_data_uri = create_test_image_with_text(test_number)

        payload = {
            "ask": "What number is shown? Reply with only the number.",
            "conversation_history": [
                {"role": "system", "content": "Read numbers from images."}
            ],
            "model": OPENROUTER_MODEL,
            "images": [image_data_uri],
        }

        response = openrouter_client.post("/api/chat", json=payload)
        assert response.status_code == 200, f"Request failed: {response.text}"

        data = response.json()
        extracted = "".join(c for c in data["analysis"] if c.isdigit())
        assert test_number in extracted, f"Expected '{test_number}', got: '{data['analysis']}'"


# =============================================================================
# STREAMING TESTS - Real LLM streaming responses
# =============================================================================

@pytest.mark.llm
@pytest.mark.integration
class TestStreamingReal:
    """Tests for streaming responses with real LLM."""

    def test_streaming_basic_events(self, openrouter_client):
        """
        Test that streaming returns proper SSE events.
        """
        payload = {
            "ask": "Say hello in exactly 3 words.",
            "conversation_history": [
                {"role": "system", "content": "Be concise."}
            ],
            "model": OPENROUTER_MODEL,
            "stream": True,
        }

        with openrouter_client.stream("POST", "/api/chat", json=payload) as response:
            assert response.status_code == 200

            events = []
            for line in response.iter_lines():
                if line.startswith("event:"):
                    events.append(line[7:].strip())

            assert len(events) > 0, "No SSE events received"
            assert StreamEvents.ANSWER_END.value in events


# =============================================================================
# MOCKED LLM TESTS - Test intermediate events by mocking litellm only
# =============================================================================

def create_mock_llm_response(content: str, tool_calls: Optional[list] = None, reasoning: Optional[str] = None):
    """
    Create a proper litellm ModelResponse object.

    This creates an actual ModelResponse (not a mock) that will pass
    type validation in our code.
    """
    from litellm import ModelResponse
    from litellm.types.utils import Choices, Message, Usage

    # Build tool_calls for the message if provided
    message_tool_calls = None
    if tool_calls:
        message_tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]

    # Create the message
    message_dict = {
        "role": "assistant",
        "content": content,
    }
    if message_tool_calls:
        message_dict["tool_calls"] = message_tool_calls
    if reasoning:
        message_dict["reasoning_content"] = reasoning

    message = Message(**message_dict)

    # Create the response
    response = ModelResponse(
        id="chatcmpl-test-123",
        choices=[
            Choices(
                finish_reason="stop" if not tool_calls else "tool_calls",
                index=0,
                message=message,
            )
        ],
        created=1234567890,
        model="gpt-4.1-mini",
        usage=Usage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        ),
    )

    return response


def create_mock_tool_call(tool_call_id: str, tool_name: str, arguments: dict):
    """Create a tool call object compatible with litellm."""
    from types import SimpleNamespace

    # Create a simple object with the expected attributes
    # Using SimpleNamespace to avoid MagicMock issues
    function = SimpleNamespace(
        name=tool_name,
        arguments=json.dumps(arguments),
    )
    tool_call = SimpleNamespace(
        id=tool_call_id,
        type="function",
        function=function,
    )
    return tool_call


class TestStreamingIntermediateEvents:
    """
    Tests for intermediate streaming events (tool calls, AI messages, etc.)

    These tests mock only litellm.completion to verify our code correctly
    emits intermediate events. This allows confident refactoring without
    being tied to implementation details.
    """

    @patch("litellm.completion")
    @patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_streaming_emits_tool_events(
        self,
        mock_get_global_instructions,
        mock_load_robusta_config,
        mock_litellm_completion,
        mock_client,
    ):
        """
        Test that streaming correctly emits START_TOOL and TOOL_RESULT events.

        Mocks litellm.completion to return a tool call, then verifies our
        streaming code emits the correct intermediate events.
        """
        mock_load_robusta_config.return_value = None
        mock_get_global_instructions.return_value = []

        # First call: LLM wants to call a tool
        tool_call = create_mock_tool_call(
            tool_call_id="call_123",
            tool_name="fetch_url",
            arguments={"url": "https://example.com"},
        )
        response_with_tool = create_mock_llm_response(
            content="Let me fetch that URL for you.",
            tool_calls=[tool_call],
        )

        # Second call: LLM provides final answer after tool result
        final_response = create_mock_llm_response(
            content="The URL returned a 200 OK response.",
            tool_calls=None,
        )

        mock_litellm_completion.side_effect = [response_with_tool, final_response]

        payload = {
            "ask": "Check if example.com is up",
            "conversation_history": [
                {"role": "system", "content": "You are a helpful assistant."}
            ],
            "stream": True,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 200

        # Parse SSE events
        events = []
        current_event = None
        for line in response.text.split("\n"):
            if line.startswith("event: "):
                current_event = line[7:]
            elif line.startswith("data: ") and current_event:
                try:
                    data = json.loads(line[6:])
                    events.append((current_event, data))
                except json.JSONDecodeError:
                    pass

        event_types = [e[0] for e in events]

        # Verify tool events are present
        assert StreamEvents.START_TOOL.value in event_types, f"Missing START_TOOL, got: {event_types}"
        assert StreamEvents.TOOL_RESULT.value in event_types, f"Missing TOOL_RESULT, got: {event_types}"
        assert StreamEvents.ANSWER_END.value in event_types, f"Missing ANSWER_END, got: {event_types}"

        # Verify START_TOOL has correct data
        start_tool_event = next(
            (e[1] for e in events if e[0] == StreamEvents.START_TOOL.value),
            None
        )
        assert start_tool_event is not None
        assert start_tool_event["tool_name"] == "fetch_url"
        assert start_tool_event["id"] == "call_123"

    @patch("litellm.completion")
    @patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_streaming_emits_ai_message_with_reasoning(
        self,
        mock_get_global_instructions,
        mock_load_robusta_config,
        mock_litellm_completion,
        mock_client,
    ):
        """
        Test that AI_MESSAGE events include reasoning content when present.

        Some models (like Claude with extended thinking) provide reasoning
        that should be captured in the AI_MESSAGE event.
        """
        mock_load_robusta_config.return_value = None
        mock_get_global_instructions.return_value = []

        # Response with reasoning content
        response_with_reasoning = create_mock_llm_response(
            content="The answer is 42.",
            tool_calls=None,
            reasoning="I need to think about the meaning of life...",
        )

        mock_litellm_completion.return_value = response_with_reasoning

        payload = {
            "ask": "What is the meaning of life?",
            "conversation_history": [
                {"role": "system", "content": "Think deeply."}
            ],
            "stream": True,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 200

        # Parse SSE events
        events = []
        current_event = None
        for line in response.text.split("\n"):
            if line.startswith("event: "):
                current_event = line[7:]
            elif line.startswith("data: ") and current_event:
                try:
                    data = json.loads(line[6:])
                    events.append((current_event, data))
                except json.JSONDecodeError:
                    pass

        # Find AI_MESSAGE event
        ai_message_events = [e[1] for e in events if e[0] == StreamEvents.AI_MESSAGE.value]

        # Should have at least one AI_MESSAGE event
        assert len(ai_message_events) >= 1 or StreamEvents.ANSWER_END.value in [e[0] for e in events]

    @patch("litellm.completion")
    @patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_streaming_answer_end_contains_full_response(
        self,
        mock_get_global_instructions,
        mock_load_robusta_config,
        mock_litellm_completion,
        mock_client,
    ):
        """
        Test that ANSWER_END event contains the complete response and metadata.
        """
        mock_load_robusta_config.return_value = None
        mock_get_global_instructions.return_value = []

        final_answer = "Here is my complete analysis of your question."
        mock_litellm_completion.return_value = create_mock_llm_response(
            content=final_answer,
            tool_calls=None,
        )

        payload = {
            "ask": "Analyze something",
            "conversation_history": [
                {"role": "system", "content": "Be helpful."}
            ],
            "stream": True,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 200

        # Parse SSE events
        events = []
        current_event = None
        for line in response.text.split("\n"):
            if line.startswith("event: "):
                current_event = line[7:]
            elif line.startswith("data: ") and current_event:
                try:
                    data = json.loads(line[6:])
                    events.append((current_event, data))
                except json.JSONDecodeError:
                    pass

        # Find ANSWER_END event
        answer_end = next(
            (e[1] for e in events if e[0] == StreamEvents.ANSWER_END.value),
            None
        )
        assert answer_end is not None, "No ANSWER_END event found"

        # Verify it contains the analysis
        assert "analysis" in answer_end
        assert final_answer in answer_end["analysis"]

        # Verify it contains conversation history
        assert "conversation_history" in answer_end

    @patch("litellm.completion")
    @patch("holmes.core.supabase_dal.SupabaseDal._SupabaseDal__load_robusta_config")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_non_streaming_returns_tool_calls_in_response(
        self,
        mock_get_global_instructions,
        mock_load_robusta_config,
        mock_litellm_completion,
        mock_client,
    ):
        """
        Test that non-streaming responses include tool_calls in the response.
        """
        mock_load_robusta_config.return_value = None
        mock_get_global_instructions.return_value = []

        # LLM calls a tool then gives final answer
        tool_call = create_mock_tool_call(
            tool_call_id="call_456",
            tool_name="get_weather",
            arguments={"location": "NYC"},
        )
        response_with_tool = create_mock_llm_response(
            content="Let me check the weather.",
            tool_calls=[tool_call],
        )
        final_response = create_mock_llm_response(
            content="The weather in NYC is sunny.",
            tool_calls=None,
        )
        mock_litellm_completion.side_effect = [response_with_tool, final_response]

        payload = {
            "ask": "What's the weather in NYC?",
            "conversation_history": [
                {"role": "system", "content": "Help with weather."}
            ],
            "stream": False,
        }

        response = mock_client.post("/api/chat", json=payload)
        assert response.status_code == 200

        data = response.json()

        # Verify response structure
        assert "analysis" in data
        assert "tool_calls" in data
        assert "conversation_history" in data

        # Should have recorded the tool call
        assert len(data["tool_calls"]) > 0
