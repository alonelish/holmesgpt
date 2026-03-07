import pytest

from holmes.core.truncation.input_context_window_limiter import (
    ContextWindowOverflowError,
)


class TestContextWindowOverflowError:
    """Test suite for ContextWindowOverflowError exception."""

    def test_error_message_contains_token_counts(self):
        error = ContextWindowOverflowError(
            current_tokens=150000,
            max_tokens=100000,
        )

        assert "150,000 tokens" in str(error)
        assert "100,000 tokens" in str(error)

    def test_error_message_asks_for_clarification(self):
        error = ContextWindowOverflowError(
            current_tokens=150000,
            max_tokens=100000,
        )

        assert "start a new conversation" in str(error)
        assert "clarify" in str(error)

    def test_error_attributes(self):
        error = ContextWindowOverflowError(
            current_tokens=50000,
            max_tokens=40000,
        )

        assert error.current_tokens == 50000
        assert error.max_tokens == 40000

    def test_error_is_exception(self):
        error = ContextWindowOverflowError(
            current_tokens=100,
            max_tokens=50,
        )

        assert isinstance(error, Exception)

        with pytest.raises(ContextWindowOverflowError) as exc_info:
            raise error

        assert exc_info.value.current_tokens == 100
        assert exc_info.value.max_tokens == 50
