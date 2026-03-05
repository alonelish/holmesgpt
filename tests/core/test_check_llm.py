from unittest.mock import patch

from holmes.core.llm import DefaultLLM


def _make_llm(model, api_key=None, api_base=None, api_version=None, args=None, is_robusta_model=False):
    """Helper to construct a DefaultLLM with check_llm un-mocked so we actually test it."""
    with patch.object(DefaultLLM, "update_custom_args"):
        llm = object.__new__(DefaultLLM)
        llm.model = model
        llm.api_key = api_key
        llm.api_base = api_base
        llm.api_version = api_version
        llm.args = args or {}
        llm.is_robusta_model = is_robusta_model
        return llm


class TestCheckLLMAzureOpenAI:
    """Tests for Azure OpenAI model validation in check_llm."""

    def test_azure_model_with_all_env_vars(self, monkeypatch):
        """Azure model passes when all required env vars are set."""
        monkeypatch.setenv("AZURE_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_API_BASE", "https://myresource.openai.azure.com")
        monkeypatch.setenv("AZURE_API_VERSION", "2024-02-01")
        llm = _make_llm("azure/gpt-4o")
        llm.check_llm("azure/gpt-4o", None, None, None)

    def test_azure_model_with_all_config(self, monkeypatch):
        """Azure model passes when api_key and api_base are provided directly."""
        monkeypatch.delenv("AZURE_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_API_BASE", raising=False)
        monkeypatch.delenv("AZURE_API_VERSION", raising=False)
        llm = _make_llm("azure/gpt-4o")
        llm.check_llm(
            "azure/gpt-4o", "test-key", "https://myresource.openai.azure.com", "2024-02-01"
        )
