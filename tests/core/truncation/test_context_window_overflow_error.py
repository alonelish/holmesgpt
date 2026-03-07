import pytest

from holmes.core.truncation.input_context_window_limiter import (
    ContextWindowOverflowError,
)


class TestContextWindowOverflowError:
    """Test suite for ContextWindowOverflowError exception."""

    def test_error_message_with_compaction_attempted(self):
        error = ContextWindowOverflowError(
            current_tokens=150000,
            max_tokens=100000,
            compaction_attempted=True,
        )

        assert "150,000 tokens" in str(error)
        assert "100,000 tokens" in str(error)
        assert "could not be summarized" in str(error)
        assert "robusta-dev/holmesgpt/issues" in str(error)

    def test_error_message_without_compaction_attempted(self):
        error = ContextWindowOverflowError(
            current_tokens=150000,
            max_tokens=100000,
            compaction_attempted=False,
        )

        assert "150,000 tokens" in str(error)
        assert "100,000 tokens" in str(error)
        assert "exceeds the context window" in str(error)
        assert "robusta-dev/holmesgpt/issues" in str(error)

    def test_error_attributes(self):
        error = ContextWindowOverflowError(
            current_tokens=50000,
            max_tokens=40000,
            compaction_attempted=True,
        )

        assert error.current_tokens == 50000
        assert error.max_tokens == 40000
        assert error.compaction_attempted is True

    def test_error_is_exception(self):
        error = ContextWindowOverflowError(
            current_tokens=100,
            max_tokens=50,
            compaction_attempted=False,
        )

        assert isinstance(error, Exception)

        with pytest.raises(ContextWindowOverflowError) as exc_info:
            raise error

        assert exc_info.value.current_tokens == 100
        assert exc_info.value.max_tokens == 50
        assert exc_info.value.compaction_attempted is False
