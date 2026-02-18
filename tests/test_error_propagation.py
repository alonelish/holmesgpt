"""Tests for LLM error propagation through HTTP endpoints and streaming formatters."""

import json
from unittest.mock import MagicMock, patch

import litellm
import pytest
from fastapi.testclient import TestClient
from litellm.exceptions import AuthenticationError, ServiceUnavailableError

from holmes.utils.stream import (
    _create_sse_error_for_exception,
    _is_rate_limit_error,
    stream_chat_formatter,
    stream_investigate_formatter,
    StreamEvents,
    StreamMessage,
)
from server import app


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# --- Tests for _is_rate_limit_error ---


class TestIsRateLimitError:
    def test_litellm_rate_limit_error(self):
        e = litellm.exceptions.RateLimitError(
            message="Rate limit exceeded", model="test", llm_provider="openai"
        )
        assert _is_rate_limit_error(e) is True

    def test_bedrock_throttling_string(self):
        e = Exception("Model is getting throttled, please retry")
        assert _is_rate_limit_error(e) is True

    def test_generic_exception_not_rate_limit(self):
        e = Exception("Something went wrong")
        assert _is_rate_limit_error(e) is False

    def test_auth_error_not_rate_limit(self):
        e = AuthenticationError(
            message="Invalid API key", model="test", llm_provider="openai"
        )
        assert _is_rate_limit_error(e) is False


# --- Tests for _create_sse_error_for_exception ---


class TestCreateSseErrorForException:
    def _parse_sse(self, sse_string: str) -> dict:
        """Parse an SSE message string into event type and data dict."""
        lines = sse_string.strip().split("\n")
        event_type = None
        data = None
        for line in lines:
            if line.startswith("event: "):
                event_type = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        return {"event": event_type, "data": data}

    def test_rate_limit_error(self):
        e = litellm.exceptions.RateLimitError(
            message="Rate limit exceeded", model="test", llm_provider="openai"
        )
        result = self._parse_sse(_create_sse_error_for_exception(e))
        assert result["event"] == StreamEvents.ERROR.value
        assert result["data"]["error_code"] == 5204
        assert result["data"]["msg"] == "Rate limit exceeded"
        assert result["data"]["success"] is False

    def test_bedrock_throttling_as_rate_limit(self):
        e = Exception("Model is getting throttled by Bedrock")
        result = self._parse_sse(_create_sse_error_for_exception(e))
        assert result["data"]["error_code"] == 5204

    def test_authentication_error(self):
        e = AuthenticationError(
            message="Invalid API key", model="test", llm_provider="openai"
        )
        result = self._parse_sse(_create_sse_error_for_exception(e))
        assert result["event"] == StreamEvents.ERROR.value
        assert result["data"]["error_code"] == 5401
        assert result["data"]["msg"] == "Authentication failed"

    def test_service_unavailable_error(self):
        e = ServiceUnavailableError(
            message="Anthropic is overloaded", model="test", llm_provider="anthropic"
        )
        result = self._parse_sse(_create_sse_error_for_exception(e))
        assert result["event"] == StreamEvents.ERROR.value
        assert result["data"]["error_code"] == 5503
        assert result["data"]["msg"] == "LLM service unavailable"

    def test_internal_server_error(self):
        e = litellm.exceptions.InternalServerError(
            message="Provider internal error", model="test", llm_provider="openai"
        )
        result = self._parse_sse(_create_sse_error_for_exception(e))
        assert result["data"]["error_code"] == 5503

    def test_api_connection_error(self):
        e = litellm.exceptions.APIConnectionError(
            message="Connection refused", model="test", llm_provider="openai"
        )
        result = self._parse_sse(_create_sse_error_for_exception(e))
        assert result["data"]["error_code"] == 5502
        assert result["data"]["msg"] == "Failed to connect to LLM service"

    def test_timeout_error(self):
        e = litellm.exceptions.Timeout(
            message="Request timed out", model="test", llm_provider="openai"
        )
        result = self._parse_sse(_create_sse_error_for_exception(e))
        assert result["data"]["error_code"] == 5502

    def test_generic_exception(self):
        e = Exception("Something unexpected happened")
        result = self._parse_sse(_create_sse_error_for_exception(e))
        assert result["data"]["error_code"] == 1
        assert "Something unexpected happened" in result["data"]["description"]

    def test_error_description_contains_exception_message(self):
        msg = "The specific error details from Anthropic"
        e = ServiceUnavailableError(
            message=msg, model="test", llm_provider="anthropic"
        )
        result = self._parse_sse(_create_sse_error_for_exception(e))
        assert msg in result["data"]["description"]


# --- Tests for streaming formatter error handling ---


class TestStreamChatFormatterErrors:
    def _parse_sse_events(self, sse_generator) -> list:
        events = []
        for sse_string in sse_generator:
            lines = sse_string.strip().split("\n")
            event_type = None
            data = None
            for line in lines:
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
            if event_type:
                events.append({"event": event_type, "data": data})
        return events

    def test_service_unavailable_mid_stream(self):
        def failing_generator():
            yield StreamMessage(
                event=StreamEvents.START_TOOL, data={"tool_name": "test"}
            )
            raise ServiceUnavailableError(
                message="Anthropic overloaded", model="test", llm_provider="anthropic"
            )

        events = self._parse_sse_events(stream_chat_formatter(failing_generator()))
        assert len(events) == 2
        assert events[0]["event"] == StreamEvents.START_TOOL.value
        assert events[1]["event"] == StreamEvents.ERROR.value
        assert events[1]["data"]["error_code"] == 5503

    def test_auth_error_mid_stream(self):
        def failing_generator():
            yield StreamMessage(event=StreamEvents.START_TOOL, data={"tool_name": "test"})
            raise AuthenticationError(
                message="Invalid key", model="test", llm_provider="openai"
            )

        events = self._parse_sse_events(stream_chat_formatter(failing_generator()))
        assert len(events) == 2
        assert events[1]["event"] == StreamEvents.ERROR.value
        assert events[1]["data"]["error_code"] == 5401

    def test_rate_limit_mid_stream(self):
        def failing_generator():
            yield StreamMessage(event=StreamEvents.START_TOOL, data={"tool_name": "test"})
            raise litellm.exceptions.RateLimitError(
                message="Too many requests", model="test", llm_provider="openai"
            )

        events = self._parse_sse_events(stream_chat_formatter(failing_generator()))
        assert len(events) == 2
        assert events[1]["data"]["error_code"] == 5204


class TestStreamInvestigateFormatterErrors:
    def _parse_sse_events(self, sse_generator) -> list:
        events = []
        for sse_string in sse_generator:
            lines = sse_string.strip().split("\n")
            event_type = None
            data = None
            for line in lines:
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
            if event_type:
                events.append({"event": event_type, "data": data})
        return events

    def test_service_unavailable_mid_stream(self):
        def failing_generator():
            yield StreamMessage(event=StreamEvents.START_TOOL, data={"tool_name": "test"})
            raise ServiceUnavailableError(
                message="Service down", model="test", llm_provider="anthropic"
            )

        events = self._parse_sse_events(
            stream_investigate_formatter(failing_generator())
        )
        assert len(events) == 2
        assert events[1]["event"] == StreamEvents.ERROR.value
        assert events[1]["data"]["error_code"] == 5503


# --- Tests for server endpoint error propagation ---


class TestServerEndpointErrorPropagation:
    """Test that LLM errors map to correct HTTP status codes on non-streaming endpoints."""

    @patch("holmes.config.Config.create_toolcalling_llm")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_chat_auth_error_returns_401(
        self, mock_get_global_instructions, mock_create_toolcalling_llm, client
    ):
        mock_ai = MagicMock()
        mock_ai.messages_call.side_effect = AuthenticationError(
            message="Invalid API key", model="test", llm_provider="openai"
        )
        mock_create_toolcalling_llm.return_value = mock_ai
        mock_get_global_instructions.return_value = []

        payload = {"ask": "test", "model": "gpt-4.1"}
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 401

    @patch("holmes.config.Config.create_toolcalling_llm")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_chat_rate_limit_returns_429(
        self, mock_get_global_instructions, mock_create_toolcalling_llm, client
    ):
        mock_ai = MagicMock()
        mock_ai.messages_call.side_effect = litellm.exceptions.RateLimitError(
            message="Rate limit exceeded", model="test", llm_provider="openai"
        )
        mock_create_toolcalling_llm.return_value = mock_ai
        mock_get_global_instructions.return_value = []

        payload = {"ask": "test", "model": "gpt-4.1"}
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 429

    @patch("holmes.config.Config.create_toolcalling_llm")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_chat_service_unavailable_returns_503(
        self, mock_get_global_instructions, mock_create_toolcalling_llm, client
    ):
        mock_ai = MagicMock()
        mock_ai.messages_call.side_effect = ServiceUnavailableError(
            message="Anthropic is overloaded",
            model="test",
            llm_provider="anthropic",
        )
        mock_create_toolcalling_llm.return_value = mock_ai
        mock_get_global_instructions.return_value = []

        payload = {"ask": "test", "model": "gpt-4.1"}
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 503
        assert "Anthropic is overloaded" in response.json()["detail"]

    @patch("holmes.config.Config.create_toolcalling_llm")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_chat_api_connection_error_returns_502(
        self, mock_get_global_instructions, mock_create_toolcalling_llm, client
    ):
        mock_ai = MagicMock()
        mock_ai.messages_call.side_effect = litellm.exceptions.APIConnectionError(
            message="Connection refused", model="test", llm_provider="openai"
        )
        mock_create_toolcalling_llm.return_value = mock_ai
        mock_get_global_instructions.return_value = []

        payload = {"ask": "test", "model": "gpt-4.1"}
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 502

    @patch("holmes.config.Config.create_toolcalling_llm")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_chat_timeout_returns_502(
        self, mock_get_global_instructions, mock_create_toolcalling_llm, client
    ):
        mock_ai = MagicMock()
        mock_ai.messages_call.side_effect = litellm.exceptions.Timeout(
            message="Request timed out", model="test", llm_provider="openai"
        )
        mock_create_toolcalling_llm.return_value = mock_ai
        mock_get_global_instructions.return_value = []

        payload = {"ask": "test", "model": "gpt-4.1"}
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 502

    @patch("holmes.config.Config.create_toolcalling_llm")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_chat_generic_error_returns_500(
        self, mock_get_global_instructions, mock_create_toolcalling_llm, client
    ):
        mock_ai = MagicMock()
        mock_ai.messages_call.side_effect = Exception("Something went wrong")
        mock_create_toolcalling_llm.return_value = mock_ai
        mock_get_global_instructions.return_value = []

        payload = {"ask": "test", "model": "gpt-4.1"}
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 500
        assert "Something went wrong" in response.json()["detail"]

    @patch("holmes.config.Config.create_toolcalling_llm")
    @patch("holmes.core.supabase_dal.SupabaseDal.get_global_instructions_for_account")
    def test_issue_chat_service_unavailable_returns_503(
        self, mock_get_global_instructions, mock_create_toolcalling_llm, client
    ):
        mock_ai = MagicMock()
        mock_ai.messages_call.side_effect = ServiceUnavailableError(
            message="Service down", model="test", llm_provider="anthropic"
        )
        mock_create_toolcalling_llm.return_value = mock_ai
        mock_get_global_instructions.return_value = []

        payload = {
            "ask": "test",
            "investigation_result": {"result": "test", "tools": []},
            "issue_type": "deployment",
            "conversation_history": [
                {"role": "system", "content": "test"},
            ],
        }
        response = client.post("/api/issue_chat", json=payload)
        assert response.status_code == 503
