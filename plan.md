# Plan: Accurately Discover Real Context Window Size When Unknown via LiteLLM

## Problem Summary

When litellm doesn't know a model's context window size (e.g. Azure custom deployment names, Bedrock cross-region models), or **knows it but is wrong** (e.g. reports 1M for `eu.anthropic.claude-sonnet-4-20250514-v1:0` when it's actually 200K), Holmes crashes with unhandled `ContextWindowExceededError` or `BadRequestError`. The current code has a `FALLBACK_CONTEXT_WINDOW_SIZE` of 200K but no mechanism to:

1. Discover the real limit dynamically
2. Recover gracefully when litellm's reported limit is wrong

## Current Architecture

**Context window resolution** (`holmes/core/llm.py:271-294`):
1. Check `self.max_context_size` (from custom_args config)
2. Check `OVERRIDE_MAX_CONTENT_SIZE` env var
3. Look up `litellm.model_cost[name]["max_input_tokens"]` with name variants
4. Fall back to `FALLBACK_CONTEXT_WINDOW_SIZE = 200000`

**Input truncation** (`holmes/core/truncation/input_context_window_limiter.py`):
- Compaction + truncation happens **before** the API call
- Uses `llm.get_context_window_size()` as the truth for sizing

**Error handling** (`holmes/core/tool_calling_llm.py:473-507`):
- Catches `BadRequestError` only for Azure tool-choice issues
- All other errors (including `ContextWindowExceededError`) propagate up and crash

## Approaches Evaluated

### Approach 1: Parse context limit from error messages and retry (RECOMMENDED - Primary)

**How it works**: When a `ContextWindowExceededError` or `BadRequestError` with a context-limit message occurs, parse the **actual** limit from the error message, update the cached context window size, re-run truncation with the corrected limit, and retry the API call.

**Error message formats that include real limits**:
- OpenAI/Azure: `"maximum context length is 128000 tokens. However, you requested 198640 tokens"`
- Bedrock: `"input length and max_tokens exceed context limit: 198640 + 8192 > 200000"`
- Anthropic: `"prompt is too long: 198640 tokens > 200000 maximum"`
- Generic: Various `"input is too long"` messages (no limit, but we can binary-search down)

**Pros**: Zero external dependencies, solves the exact problem, handles litellm-is-wrong case, works for ALL providers
**Cons**: Relies on parsing error messages (fragile to format changes), first call fails before recovery

### Approach 2: OpenRouter `/api/v1/models` API lookup (EVALUATED - Not recommended as primary)

**How it works**: Query OpenRouter's free models endpoint to look up `context_length` for a model.

**Pros**: Real-time data, covers 400+ models
**Cons**: Only works if user has OpenRouter, doesn't help with custom Azure/Bedrock deployments (the main problem), adds HTTP dependency at startup, model names may not match

### Approach 3: Test completion call at startup (EVALUATED - Not recommended)

**How it works**: Send a minimal `completion()` call and inspect response metadata for available tokens.

**Pros**: Would give real limit
**Cons**: Standard completion responses don't include "remaining context" info, adds latency and cost at startup, still doesn't reveal the limit unless it fails

### Approach 4: `litellm.get_model_info()` (EVALUATED - Already effectively used)

The current code already queries `litellm.model_cost` which is the same data source. The problem is litellm's data is wrong for certain models. This doesn't help.

## Recommended Implementation: Approach 1 - Error-Based Discovery with Retry

### Step 1: Add `ContextWindowExceededError` handling in `tool_calling_llm.py`

In both `call()` and `call_stream()`, catch `ContextWindowExceededError` (from litellm.exceptions) and `BadRequestError` where the message indicates a context window issue. Parse the real limit from the error message.

**In `tool_calling_llm.py` `call()` method** (around line 473):

```python
from litellm.exceptions import ContextWindowExceededError

# Inside the try/except block, add before the generic Exception catch:
except ContextWindowExceededError as e:
    parsed_limit = _parse_context_limit_from_error(str(e))
    if parsed_limit and not context_window_already_corrected:
        logging.warning(
            f"Context window exceeded. LiteLLM reported {self.llm.get_context_window_size()} tokens "
            f"but actual limit is {parsed_limit}. Retrying with corrected limit."
        )
        self.llm.update_context_window_size(parsed_limit)
        context_window_already_corrected = True
        continue  # retry the current iteration with corrected size
    else:
        raise  # already tried correction or couldn't parse - give up
except BadRequestError as e:
    error_msg = str(e)
    # Check if this is a context window error disguised as BadRequest
    parsed_limit = _parse_context_limit_from_error(error_msg)
    if parsed_limit and not context_window_already_corrected:
        # same recovery as above
        ...
    elif "Unrecognized request arguments supplied: tool_choice, tools" in error_msg:
        # existing Azure handling
        ...
    else:
        raise
```

### Step 2: Add error message parser

Add a function `_parse_context_limit_from_error(error_message: str) -> Optional[int]` in a new file or in `llm.py`:

```python
import re

# Patterns that include the actual limit
CONTEXT_LIMIT_PATTERNS = [
    # Bedrock: "input length and max_tokens exceed context limit: 198640 + 8192 > 200000"
    re.compile(r"exceed context limit:.*?>\s*(\d+)"),
    # OpenAI/Azure: "maximum context length is 128000 tokens"
    re.compile(r"maximum context length is (\d+)"),
    # Anthropic: "prompt is too long: 198640 tokens > 200000 maximum"
    re.compile(r">\s*(\d+)\s*maximum"),
    # Generic: "context limit: N" or "context window: N"
    re.compile(r"context (?:limit|window)[:\s]+(\d+)"),
]

def _parse_context_limit_from_error(error_message: str) -> Optional[int]:
    for pattern in CONTEXT_LIMIT_PATTERNS:
        match = pattern.search(error_message)
        if match:
            limit = int(match.group(1))
            if limit > 0:
                return limit
    return None
```

### Step 3: Add `update_context_window_size()` to `DefaultLLM`

In `holmes/core/llm.py`, add a method to the `DefaultLLM` class:

```python
def update_context_window_size(self, new_size: int) -> None:
    """Update the context window size after discovering the real limit from an API error."""
    logging.info(
        f"Updating context window size from {self.get_context_window_size()} to {new_size} "
        f"for model {self.model}"
    )
    self.max_context_size = new_size
```

This leverages the existing `self.max_context_size` field which is already checked first in `get_context_window_size()`.

### Step 4: Handle the "no limit in error" case

For errors like `"Input is too long for requested model."` that don't include the actual limit, apply a conservative reduction: set the context window to half of what we thought it was, and retry.

```python
if not parsed_limit:
    # Can't parse exact limit - reduce by half and retry
    current_size = self.llm.get_context_window_size()
    reduced_size = current_size // 2
    if reduced_size >= 8192 and not context_window_already_corrected:  # minimum viable context
        logging.warning(
            f"Context window error without explicit limit. "
            f"Reducing from {current_size} to {reduced_size} and retrying."
        )
        self.llm.update_context_window_size(reduced_size)
        context_window_already_corrected = True
        continue
```

### Changes Summary

| File | Change |
|------|--------|
| `holmes/core/llm.py` | Add `update_context_window_size()` method |
| `holmes/core/tool_calling_llm.py` | Add `ContextWindowExceededError` + context-limit `BadRequestError` handling with retry in both `call()` and `call_stream()` |
| `holmes/core/context_window_error_parser.py` (new) | `_parse_context_limit_from_error()` regex parser |
| `tests/core/test_context_window_error_parser.py` (new) | Unit tests for error message parsing |

### Why Not the Other Approaches?

- **OpenRouter API**: Only helps if user happens to be using OpenRouter; the primary pain point is Azure/Bedrock custom deployments which OpenRouter can't help with
- **Test completion**: Doesn't reveal context window size; would add startup latency
- **Better litellm data**: We can't control litellm's model_cost dict; even if it's updated, custom deployment names will never be in it

The error-based approach is the only one that works **universally** regardless of provider, because every provider tells you the real limit when you hit it.
