import time
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pytest
from pydantic import ValidationError

from holmes.core.models import ChatResponse
from holmes.core.scheduled_prompts import (
    ScheduledPrompt,
    ScheduledPromptsExecutor,
    ScheduledPromptsHeartbeatSpan,
)
from holmes.core.supabase_dal import RunStatus


@pytest.fixture
def mock_dal():
    """Create a mock SupabaseDal."""
    dal = MagicMock()
    dal.enabled = True
    dal.claim_scheduled_prompt_run = MagicMock(return_value=None)
    dal.update_run_status = MagicMock(return_value=True)
    dal.finish_scheduled_prompt_run = MagicMock(return_value=True)
    dal.get_global_instructions_for_account = MagicMock(return_value=[])
    return dal


@pytest.fixture
def mock_config():
    """Create a mock Config."""
    config = MagicMock()
    config.get_models_list = MagicMock(return_value=["gpt-4.1", "gpt-4o"])
    config.get_runbook_catalog = MagicMock(return_value={})
    config.create_toolcalling_llm = MagicMock()
    return config


@pytest.fixture
def mock_chat_function():
    """Create a mock chat function."""

    def chat_func(request):
        return ChatResponse(
            analysis="Test analysis",
            conversation_history=[],
            tool_calls=[],
            follow_up_actions=[],
            metadata={"test": "metadata"},
        )

    return Mock(side_effect=chat_func)


@pytest.fixture
def sample_scheduled_prompt_payload():
    """Create a sample scheduled prompt payload."""
    return {
        "id": "test-run-123",
        "scheduled_prompt_definition_id": "def-456",
        "account_id": "acc-789",
        "cluster_name": "test-cluster",
        "model_name": "gpt-4.1",
        "prompt": {"raw_prompt": "What is the status of my cluster?"},
        "status": "pending",
        "msg": None,
        "created_at": datetime.now().isoformat(),
        "last_heartbeat_at": datetime.now().isoformat(),
        "metadata": {"test": "value"},
    }


@pytest.fixture
def executor(mock_dal, mock_config, mock_chat_function):
    """Create a ScheduledPromptsExecutor instance."""
    return ScheduledPromptsExecutor(
        dal=mock_dal, config=mock_config, chat_function=mock_chat_function
    )


class TestScheduledPromptsExecutor:
    """Tests for ScheduledPromptsExecutor."""

    def test_executor_initialization(self, executor, mock_dal, mock_config):
        """Test that executor initializes correctly."""
        assert executor.dal == mock_dal
        assert executor.config == mock_config
        assert executor.running is False
        assert executor.thread is None
        assert executor.holmes_id is not None

    def test_start_executor_disabled_dal(self, mock_config, mock_chat_function):
        """Test starting executor with disabled DAL."""
        dal = MagicMock()
        dal.enabled = False
        executor = ScheduledPromptsExecutor(
            dal=dal, config=mock_config, chat_function=mock_chat_function
        )
        executor.start()
        assert executor.running is False
        assert executor.thread is None

    def test_start_executor_success(self, executor):
        """Test successfully starting the executor."""
        executor.start()
        assert executor.running is True
        assert executor.thread is not None
        assert executor.thread.daemon is True
        executor.stop()

    def test_start_executor_already_running(self, executor):
        """Test starting executor when already running."""
        executor.start()
        initial_thread = executor.thread
        executor.start()  # Try to start again
        assert executor.thread == initial_thread  # Same thread
        executor.stop()

    def test_stop_executor(self, executor):
        """Test stopping the executor."""
        executor.start()
        assert executor.running is True
        executor.stop()
        assert executor.running is False

    @patch("holmes.core.scheduled_prompts.executor.time.sleep")
    def test_process_next_prompt_no_payload(self, mock_sleep, executor, mock_dal):
        """Test processing when no prompt is available."""
        mock_dal.claim_scheduled_prompt_run.return_value = None
        executor._process_next_prompt()
        mock_sleep.assert_called_once()
        mock_dal.update_run_status.assert_not_called()

    def test_process_next_prompt_invalid_payload(self, executor, mock_dal):
        """Test processing with invalid payload."""
        mock_dal.claim_scheduled_prompt_run.return_value = {
            "id": "test-123",
            # Missing required fields
        }
        executor._process_next_prompt()
        # Should log error but not crash
        mock_dal.update_run_status.assert_not_called()
        mock_dal.finish_scheduled_prompt_run.assert_not_called()

    def test_process_next_prompt_success(
        self, executor, mock_dal, sample_scheduled_prompt_payload
    ):
        """Test successfully processing a prompt."""
        mock_dal.claim_scheduled_prompt_run.return_value = (
            sample_scheduled_prompt_payload
        )
        executor._process_next_prompt()

        # Should finish successfully
        mock_dal.finish_scheduled_prompt_run.assert_called_once()
        call_args = mock_dal.finish_scheduled_prompt_run.call_args
        assert call_args.kwargs["status"] == RunStatus.COMPLETED
        assert call_args.kwargs["run_id"] == "test-run-123"

    def test_process_next_prompt_execution_error(
        self, mock_dal, mock_config, sample_scheduled_prompt_payload
    ):
        """Test handling execution error."""
        # Create chat function that raises an error
        def error_chat_func(request):
            raise Exception("Test error")

        executor = ScheduledPromptsExecutor(
            dal=mock_dal, config=mock_config, chat_function=Mock(side_effect=error_chat_func)
        )

        mock_dal.claim_scheduled_prompt_run.return_value = (
            sample_scheduled_prompt_payload
        )

        executor._process_next_prompt()

        # Should finish with failed status
        mock_dal.finish_scheduled_prompt_run.assert_called_once()
        call_args = mock_dal.finish_scheduled_prompt_run.call_args
        assert call_args.kwargs["status"] == RunStatus.FAILED
        assert "Test error" in call_args.kwargs["result"]["error"]

    def test_execute_scheduled_prompt_invalid_model(
        self, mock_dal, mock_chat_function, sample_scheduled_prompt_payload
    ):
        """Test executing prompt with invalid model."""
        # Create config that returns limited model list
        config = MagicMock()
        config.get_models_list = MagicMock(return_value=["gpt-4.1"])
        config.get_runbook_catalog = MagicMock(return_value={})

        executor = ScheduledPromptsExecutor(
            dal=mock_dal, config=config, chat_function=mock_chat_function
        )

        sample_scheduled_prompt_payload["model_name"] = "invalid-model"
        mock_dal.claim_scheduled_prompt_run.return_value = (
            sample_scheduled_prompt_payload
        )

        executor._process_next_prompt()

        # Should finish with failed status
        mock_dal.finish_scheduled_prompt_run.assert_called_once()
        call_args = mock_dal.finish_scheduled_prompt_run.call_args
        assert call_args.kwargs["status"] == RunStatus.FAILED
        assert "invalid-model" in call_args.kwargs["result"]["error"]

    def test_extract_prompt_text_string(self, executor):
        """Test extracting prompt text from string."""
        result = executor._extract_prompt_text("test prompt")
        assert result == "test prompt"

    def test_extract_prompt_text_dict_with_raw(self, executor):
        """Test extracting prompt text from dict with raw_prompt."""
        result = executor._extract_prompt_text(
            {"raw_prompt": "test prompt", "other": "data"}
        )
        assert result == "test prompt"

    def test_extract_prompt_text_dict_without_raw(self, executor):
        """Test extracting prompt text from dict without raw_prompt."""
        prompt_dict = {"other": "data"}
        result = executor._extract_prompt_text(prompt_dict)
        assert result == str(prompt_dict)

    @patch("holmes.core.scheduled_prompts.executor.urlopen")
    def test_fetch_additional_system_prompt_success(self, mock_urlopen, executor):
        """Test fetching additional system prompt successfully."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"additional_system_prompt": "test prompt"}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = executor._fetch_additional_system_prompt()
        assert result == "test prompt"

    @patch("holmes.core.scheduled_prompts.executor.urlopen")
    def test_fetch_additional_system_prompt_error(self, mock_urlopen, executor):
        """Test fetching additional system prompt with error."""
        # Make urlopen raise an exception when called
        mock_urlopen.side_effect = TimeoutError("Network error")

        result = executor._fetch_additional_system_prompt(fallback="fallback prompt")
        assert result == "fallback prompt"

    def test_execute_prompt_with_heartbeat_span(
        self, executor, mock_dal, sample_scheduled_prompt_payload
    ):
        """Test that execute_prompt creates and passes heartbeat span."""
        sp = ScheduledPrompt(**sample_scheduled_prompt_payload)
        response = executor._execute_prompt(sp)

        # Verify chat function was called with trace_span
        assert executor.chat_function.called
        call_args = executor.chat_function.call_args
        assert call_args.args[0].trace_span is not None
        assert isinstance(
            call_args.args[0].trace_span, ScheduledPromptsHeartbeatSpan
        )
        assert isinstance(response, ChatResponse)


class TestScheduledPromptsHeartbeatSpan:
    """Tests for ScheduledPromptsHeartbeatSpan."""

    @pytest.fixture
    def scheduled_prompt(self, sample_scheduled_prompt_payload):
        """Create a ScheduledPrompt instance."""
        return ScheduledPrompt(**sample_scheduled_prompt_payload)

    @pytest.fixture
    def heartbeat_span(self, scheduled_prompt, mock_dal):
        """Create a ScheduledPromptsHeartbeatSpan instance."""
        return ScheduledPromptsHeartbeatSpan(
            sp=scheduled_prompt, dal=mock_dal, heartbeat_interval_seconds=1
        )

    def test_heartbeat_span_initialization(
        self, heartbeat_span, scheduled_prompt, mock_dal
    ):
        """Test heartbeat span initialization."""
        assert heartbeat_span.sp == scheduled_prompt
        assert heartbeat_span.dal == mock_dal
        assert heartbeat_span.heartbeat_interval_seconds == 1
        assert heartbeat_span.last_heartbeat_time is not None

    def test_heartbeat_triggered_on_start_span(
        self, heartbeat_span, scheduled_prompt, mock_dal
    ):
        """Test heartbeat is triggered on start_span."""
        # Wait for interval to pass
        time.sleep(1.1)

        new_span = heartbeat_span.start_span(name="test", span_type="tool")

        # Verify heartbeat was sent
        mock_dal.update_run_status.assert_called_once_with(
            run_id=scheduled_prompt.id, status=RunStatus.RUNNING
        )
        assert isinstance(new_span, ScheduledPromptsHeartbeatSpan)

    def test_heartbeat_triggered_on_log(
        self, heartbeat_span, scheduled_prompt, mock_dal
    ):
        """Test heartbeat is triggered on log."""
        # Wait for interval to pass
        time.sleep(1.1)

        heartbeat_span.log({"test": "data"})

        # Verify heartbeat was sent
        mock_dal.update_run_status.assert_called_once_with(
            run_id=scheduled_prompt.id, status=RunStatus.RUNNING
        )

    def test_heartbeat_rate_limited(self, heartbeat_span, mock_dal):
        """Test heartbeat is rate limited."""
        # Call multiple times quickly
        heartbeat_span.log()
        heartbeat_span.log()
        heartbeat_span.log()

        # Should not send heartbeat (interval not passed)
        mock_dal.update_run_status.assert_not_called()

    def test_heartbeat_updates_last_time(
        self, heartbeat_span, scheduled_prompt, mock_dal
    ):
        """Test heartbeat updates last heartbeat time."""
        initial_time = heartbeat_span.last_heartbeat_time

        # Wait for interval to pass
        time.sleep(1.1)

        heartbeat_span.log()

        # Verify time was updated
        assert heartbeat_span.last_heartbeat_time > initial_time

    def test_heartbeat_handles_dal_error(
        self, heartbeat_span, scheduled_prompt, mock_dal
    ):
        """Test heartbeat handles DAL errors gracefully."""
        mock_dal.update_run_status.side_effect = Exception("DAL error")

        # Wait for interval to pass
        time.sleep(1.1)

        # Should not raise exception
        heartbeat_span.log()

        mock_dal.update_run_status.assert_called_once()


class TestScheduledPromptModel:
    """Tests for ScheduledPrompt model."""

    def test_scheduled_prompt_valid(self, sample_scheduled_prompt_payload):
        """Test creating valid ScheduledPrompt."""
        sp = ScheduledPrompt(**sample_scheduled_prompt_payload)
        assert sp.id == "test-run-123"
        assert sp.model_name == "gpt-4.1"
        assert sp.prompt == {"raw_prompt": "What is the status of my cluster?"}

    def test_scheduled_prompt_missing_required_field(self):
        """Test ScheduledPrompt with missing required field."""
        with pytest.raises(ValidationError):
            ScheduledPrompt(
                id="test",
                # Missing other required fields
            )

    def test_scheduled_prompt_optional_fields(self, sample_scheduled_prompt_payload):
        """Test ScheduledPrompt with optional fields set to None."""
        sample_scheduled_prompt_payload["scheduled_prompt_definition_id"] = None
        sample_scheduled_prompt_payload["msg"] = None
        sample_scheduled_prompt_payload["metadata"] = None

        sp = ScheduledPrompt(**sample_scheduled_prompt_payload)
        assert sp.scheduled_prompt_definition_id is None
        assert sp.msg is None
        assert sp.metadata is None
