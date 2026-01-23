import pytest

from holmes.core.llm import get_provider_suggestion


class TestGetProviderSuggestion:
    """Tests for the get_provider_suggestion function that helps users fix model names."""

    def test_claude_without_prefix(self):
        """Model names containing 'claude' should suggest anthropic prefix."""
        result = get_provider_suggestion("claude-opus-4.5")
        assert result is not None
        assert "anthropic" in result.lower()
        assert "anthropic/claude-opus-4.5" in result
        assert "docs.litellm.ai" in result

    def test_opus_without_prefix(self):
        """Model names containing 'opus' should suggest anthropic prefix."""
        result = get_provider_suggestion("opus-4")
        assert result is not None
        assert "anthropic" in result.lower()
        assert "anthropic/opus-4" in result

    def test_sonnet_without_prefix(self):
        """Model names containing 'sonnet' should suggest anthropic prefix."""
        result = get_provider_suggestion("sonnet-4-5")
        assert result is not None
        assert "anthropic" in result.lower()
        assert "anthropic/sonnet-4-5" in result

    def test_haiku_without_prefix(self):
        """Model names containing 'haiku' should suggest anthropic prefix."""
        result = get_provider_suggestion("haiku-3-5")
        assert result is not None
        assert "anthropic" in result.lower()
        assert "anthropic/haiku-3-5" in result

    def test_model_with_prefix_returns_none(self):
        """Models that already have a provider prefix should return None."""
        result = get_provider_suggestion("anthropic/claude-sonnet-4-5")
        assert result is None

    def test_model_with_openai_prefix_returns_none(self):
        """Models with any provider prefix should return None."""
        result = get_provider_suggestion("openai/gpt-4")
        assert result is None

    def test_unknown_model_returns_none(self):
        """Unknown model names without recognized patterns should return None."""
        result = get_provider_suggestion("some-random-model")
        assert result is None

    def test_case_insensitive_detection(self):
        """Pattern detection should be case-insensitive."""
        result = get_provider_suggestion("CLAUDE-OPUS-4")
        assert result is not None
        assert "anthropic" in result.lower()

        result = get_provider_suggestion("Claude-Sonnet-4")
        assert result is not None
        assert "anthropic" in result.lower()
