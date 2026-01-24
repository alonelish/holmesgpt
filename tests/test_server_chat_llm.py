"""
Real LLM tests for /api/chat endpoint.

These tests use actual LLM calls via OpenRouter to verify:
1. Structured output (JSON schema responses)
2. Image extraction (real image content analysis)
3. Streaming responses

Requirements:
- OPENROUTER_API_KEY environment variable must be set
- Tests are marked with 'llm' and 'integration' markers
"""

import base64
import json
import os
from io import BytesIO
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Skip all tests in this module if OPENROUTER_API_KEY is not set
pytestmark = [
    pytest.mark.llm,
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set - skipping real LLM tests",
    ),
]

# Model to use for tests - Claude Haiku 4.5 via OpenRouter
TEST_MODEL = "openrouter/anthropic/claude-haiku-4.5"


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

    # Create a white image
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)

    # Use default font (no external font file needed)
    try:
        # Try to use a larger built-in font
        font = ImageFont.load_default(size=20)
    except TypeError:
        # Older PIL versions don't support size parameter
        font = ImageFont.load_default()

    # Draw text in black, centered
    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    x = (width - text_width) // 2
    y = (height - text_height) // 2
    draw.text((x, y), text, fill="black", font=font)

    # Convert to base64
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    img_bytes = buffer.getvalue()
    b64_string = base64.b64encode(img_bytes).decode("utf-8")

    return f"data:image/png;base64,{b64_string}"


@pytest.fixture
def client():
    """
    Create a test client for the FastAPI app with OpenRouter model support.

    This fixture patches the server's config to use our OpenRouter model directly,
    bypassing the Robusta AI model list.
    """
    from holmes.core.llm import ModelEntry

    # Import server to get access to config
    import server

    # Create a model entry for our OpenRouter model
    openrouter_model_entry = ModelEntry(
        name=TEST_MODEL,
        model=TEST_MODEL,
        api_key=None,  # Will use OPENROUTER_API_KEY from env
    )

    # Save the original get_model_params method
    original_get_model_params = server.config.llm_model_registry.get_model_params

    # Patch the model registry's get_model_params to return our model
    def mock_get_model_params(model_key=None):
        if model_key == TEST_MODEL:
            return openrouter_model_entry.model_copy()
        # For other models, use the original
        return original_get_model_params(model_key)

    # Apply the patch
    server.config.llm_model_registry.get_model_params = mock_get_model_params

    try:
        yield TestClient(server.app)
    finally:
        # Restore the original method
        server.config.llm_model_registry.get_model_params = original_get_model_params


def parse_json_response(text: str) -> dict:
    """
    Parse JSON from LLM response, handling markdown code blocks.

    Claude models often wrap JSON in markdown code blocks.
    """
    # Strip markdown code blocks if present
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        # Try to extract JSON from generic code block
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1].strip()
            # Remove language identifier if present (e.g., "json\n{...")
            if text and not text.startswith("{") and not text.startswith("["):
                lines = text.split("\n", 1)
                if len(lines) > 1:
                    text = lines[1].strip()
    return json.loads(text)


class TestChatStructuredOutput:
    """Tests for structured output (JSON schema) responses via prompting."""

    def test_structured_output_basic_schema(self, client):
        """
        Test that the LLM returns properly structured JSON when prompted.

        We ask the LLM to analyze a simple statement and return structured data
        with specific fields. Uses explicit prompting since Claude via OpenRouter
        doesn't fully support OpenAI-style response_format.
        """
        payload = {
            "ask": """Analyze the sentiment of this text: 'I absolutely love this amazing product! It works perfectly and exceeded all my expectations.'

Return ONLY a JSON object with this exact structure (no other text):
{
    "sentiment": "positive" or "negative" or "neutral",
    "confidence": number between 0 and 1,
    "keywords": ["array", "of", "keywords"]
}""",
            "conversation_history": [
                {"role": "system", "content": "You are a sentiment analysis assistant. Always respond with ONLY valid JSON, no explanation or markdown."}
            ],
            "model": TEST_MODEL,
        }

        response = client.post("/api/chat", json=payload)
        assert response.status_code == 200, f"Request failed: {response.text}"

        data = response.json()
        assert "analysis" in data, f"Response missing 'analysis': {data}"
        assert data["analysis"], f"Analysis is empty. Full response: {data}"

        try:
            analysis = parse_json_response(data["analysis"])
        except json.JSONDecodeError as e:
            pytest.fail(f"Failed to parse analysis as JSON: {e}\nRaw analysis: {data['analysis']}")

        # Verify schema compliance
        assert "sentiment" in analysis, f"Missing 'sentiment' in response: {analysis}"
        assert analysis["sentiment"] in ["positive", "negative", "neutral"], f"Invalid sentiment: {analysis['sentiment']}"
        assert "confidence" in analysis, f"Missing 'confidence' in response: {analysis}"
        assert isinstance(analysis["confidence"], (int, float)), f"Confidence not a number: {analysis['confidence']}"
        assert 0 <= analysis["confidence"] <= 1, f"Confidence out of range: {analysis['confidence']}"
        assert "keywords" in analysis, f"Missing 'keywords' in response: {analysis}"
        assert isinstance(analysis["keywords"], list), f"Keywords not a list: {analysis['keywords']}"

        # The text is clearly positive, so verify the LLM detected it
        assert analysis["sentiment"] == "positive", f"Expected positive sentiment, got {analysis['sentiment']}"

    def test_structured_output_complex_schema(self, client):
        """
        Test structured output with a more complex nested schema.

        This tests that the LLM can handle nested objects and arrays.
        """
        payload = {
            "ask": """Analyze this task: 'Deploy a new Kubernetes cluster with 3 nodes, configure monitoring with Prometheus, and set up alerting.'

Return ONLY a JSON object with this exact structure (no other text):
{
    "task_summary": "brief summary string",
    "priority": "low" or "medium" or "high" or "critical",
    "estimated_steps": integer,
    "requirements": {
        "skills_needed": ["array", "of", "skills"],
        "time_estimate_minutes": integer
    }
}""",
            "conversation_history": [
                {"role": "system", "content": "You are a task analysis assistant. Always respond with ONLY valid JSON, no explanation or markdown."}
            ],
            "model": TEST_MODEL,
        }

        response = client.post("/api/chat", json=payload)
        assert response.status_code == 200, f"Request failed: {response.text}"

        data = response.json()

        try:
            analysis = parse_json_response(data["analysis"])
        except json.JSONDecodeError as e:
            pytest.fail(f"Failed to parse analysis as JSON: {e}\nRaw analysis: {data['analysis']}")

        # Verify nested structure
        assert "task_summary" in analysis
        assert "priority" in analysis
        assert analysis["priority"] in ["low", "medium", "high", "critical"]
        assert "estimated_steps" in analysis
        assert isinstance(analysis["estimated_steps"], int)
        assert "requirements" in analysis
        assert "skills_needed" in analysis["requirements"]
        assert isinstance(analysis["requirements"]["skills_needed"], list)
        assert "time_estimate_minutes" in analysis["requirements"]
        assert isinstance(analysis["requirements"]["time_estimate_minutes"], int)


class TestChatImageExtraction:
    """Tests for image content extraction using real LLM vision capabilities."""

    def test_image_text_extraction(self, client):
        """
        Test that the LLM can extract text from an image.

        We create an image with specific text that the LLM couldn't possibly
        know in advance, then verify it correctly reads the text.
        """
        # Create a unique code that the LLM must read from the image
        secret_code = "HOLMES-7X9K2M"
        image_data_uri = create_test_image_with_text(secret_code)

        payload = {
            "ask": "What text do you see in this image? Please respond with ONLY the exact text, nothing else.",
            "conversation_history": [
                {"role": "system", "content": "You are a helpful assistant that reads text from images. Respond with only the text you see, no explanation."}
            ],
            "model": TEST_MODEL,
            "images": [image_data_uri],
        }

        response = client.post("/api/chat", json=payload)
        assert response.status_code == 200, f"Request failed: {response.text}"

        data = response.json()
        analysis = data["analysis"].strip()

        # The LLM should have read the secret code from the image
        # Allow for minor formatting differences (quotes, etc.)
        assert secret_code in analysis or secret_code.replace("-", "") in analysis.replace("-", ""), (
            f"Expected LLM to read '{secret_code}' from image, but got: '{analysis}'"
        )

    def test_image_with_numbers(self, client):
        """
        Test image extraction with numerical content.

        Creates an image with a specific number that the LLM must identify.
        """
        test_number = "42857"
        image_data_uri = create_test_image_with_text(test_number)

        payload = {
            "ask": "What number is shown in this image? Reply with only the number.",
            "conversation_history": [
                {"role": "system", "content": "You read numbers from images. Reply with only the number, no other text."}
            ],
            "model": TEST_MODEL,
            "images": [image_data_uri],
        }

        response = client.post("/api/chat", json=payload)
        assert response.status_code == 200, f"Request failed: {response.text}"

        data = response.json()
        analysis = data["analysis"].strip()

        # Extract digits from response to handle any formatting
        extracted_digits = "".join(c for c in analysis if c.isdigit())
        assert test_number in extracted_digits, (
            f"Expected LLM to read '{test_number}' from image, but got: '{analysis}'"
        )

    def test_image_advanced_format_with_detail(self, client):
        """
        Test image with advanced format including detail parameter.
        """
        test_text = "HIGH-RES"
        image_data_uri = create_test_image_with_text(test_text, width=400, height=200)

        payload = {
            "ask": "What text is in this image?",
            "conversation_history": [
                {"role": "system", "content": "Read text from images."}
            ],
            "model": TEST_MODEL,
            "images": [
                {
                    "url": image_data_uri,
                    "detail": "high",
                }
            ],
        }

        response = client.post("/api/chat", json=payload)
        assert response.status_code == 200, f"Request failed: {response.text}"

        data = response.json()
        # Just verify it processed without error and got a response
        assert "analysis" in data
        assert len(data["analysis"]) > 0


class TestChatStreaming:
    """Tests for streaming responses from /api/chat endpoint."""

    def test_streaming_basic(self, client):
        """
        Test basic streaming functionality.

        Verifies that streaming returns SSE events with proper format.
        """
        payload = {
            "ask": "Count from 1 to 5, one number per line.",
            "conversation_history": [
                {"role": "system", "content": "You are a helpful assistant."}
            ],
            "model": TEST_MODEL,
            "stream": True,
        }

        # Use stream=True in the client to get streaming response
        with client.stream("POST", "/api/chat", json=payload) as response:
            assert response.status_code == 200

            events = []
            content_chunks = []

            for line in response.iter_lines():
                if not line:
                    continue

                # SSE format: "event: <type>\ndata: <json>"
                if line.startswith("event:"):
                    event_type = line[7:].strip()
                    events.append(event_type)
                elif line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                        if "content" in data:
                            content_chunks.append(data["content"])
                    except json.JSONDecodeError:
                        pass  # Some data lines might not be JSON

            # Should have received at least some events
            assert len(events) > 0, "No SSE events received"

            # Should have an ai_answer_end event
            assert "ai_answer_end" in events, f"Expected 'ai_answer_end' event, got: {events}"

    def test_streaming_with_structured_output(self, client):
        """
        Test streaming with structured output via prompting.

        Even with streaming, the final response should be valid JSON.
        """
        payload = {
            "ask": """Say hello and count the words in your greeting.

Return ONLY a JSON object with this exact structure (no other text):
{
    "greeting": "your greeting string",
    "word_count": integer
}""",
            "conversation_history": [
                {"role": "system", "content": "Respond with ONLY valid JSON, no explanation or markdown."}
            ],
            "model": TEST_MODEL,
            "stream": True,
        }

        final_analysis = None

        with client.stream("POST", "/api/chat", json=payload) as response:
            assert response.status_code == 200

            for line in response.iter_lines():
                if not line:
                    continue

                if line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                        if "analysis" in data:
                            final_analysis = data["analysis"]
                    except json.JSONDecodeError:
                        pass

        # Verify we got a valid JSON response
        assert final_analysis is not None, "No analysis in streaming response"
        parsed = parse_json_response(final_analysis)
        assert "greeting" in parsed
        assert "word_count" in parsed

    def test_streaming_token_events(self, client):
        """
        Test that streaming includes proper token/message events.
        """
        payload = {
            "ask": "Write a haiku about clouds.",
            "conversation_history": [
                {"role": "system", "content": "You are a poet."}
            ],
            "model": TEST_MODEL,
            "stream": True,
        }

        event_types = set()

        with client.stream("POST", "/api/chat", json=payload) as response:
            assert response.status_code == 200

            for line in response.iter_lines():
                if line.startswith("event:"):
                    event_types.add(line[7:].strip())

        # Should have various event types
        assert "ai_answer_end" in event_types, f"Missing 'ai_answer_end', got: {event_types}"


class TestChatCombined:
    """Tests combining multiple features (images + structured output + streaming)."""

    def test_image_with_structured_output(self, client):
        """
        Test image analysis with structured JSON output via prompting.

        Combines vision capabilities with structured output.
        """
        test_code = "ABC123"
        image_data_uri = create_test_image_with_text(test_code)

        payload = {
            "ask": f"""Analyze this image and extract any text you see.

Return ONLY a JSON object with this exact structure (no other text):
{{
    "text_found": "the exact text you see",
    "has_text": true or false,
    "character_count": integer (count of characters in the text)
}}""",
            "conversation_history": [
                {"role": "system", "content": "Analyze images and respond with ONLY valid JSON, no explanation or markdown."}
            ],
            "model": TEST_MODEL,
            "images": [image_data_uri],
        }

        response = client.post("/api/chat", json=payload)
        assert response.status_code == 200, f"Request failed: {response.text}"

        data = response.json()

        try:
            analysis = parse_json_response(data["analysis"])
        except json.JSONDecodeError as e:
            pytest.fail(f"Failed to parse analysis as JSON: {e}\nRaw analysis: {data['analysis']}")

        assert analysis["has_text"] is True, f"Expected has_text=True, got {analysis}"
        # Check text found - allow for minor variations in reading
        text_found = analysis["text_found"].replace(" ", "").replace("-", "")
        expected = test_code.replace(" ", "").replace("-", "")
        assert expected in text_found or text_found in expected, (
            f"Expected to find '{test_code}' in text_found, got '{analysis['text_found']}'"
        )
