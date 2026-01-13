# OpenRouter

Configure HolmesGPT to talk to OpenRouter using either the OpenAI-compatible endpoint or the LiteLLM OpenRouter provider.

## Methods

### Method 1: OpenAI-compatible (minimal)

Set the base URL and key, then call with an OpenAI-style model name. Only `OPENAI_API_BASE` and `OPENAI_API_KEY` are required.

```bash
export OPENAI_API_BASE="https://openrouter.ai/api/v1"
export OPENAI_API_KEY="sk-or-..."  # your OpenRouter key
holmes ask "hello" --model="openrouter/anthropic/claude-opus-4.5" --no-interactive
```

### Method 2: LiteLLM OpenRouter provider

```bash
export OPENAI_API_BASE="https://openrouter.ai/api/v1"
export OPENAI_API_KEY="sk-or-..."  # your OpenRouter key
holmes ask "hello" --model="openrouter/anthropic/claude-opus-4.5" --no-interactive
```

You can swap the model ID for any OpenRouter model.
