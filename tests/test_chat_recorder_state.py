"""Unit tests for server._build_chat_recorder_state.

Specifically covers the is_internal derivation logic:
- explicit chat_request.is_internal=True wins
- explicit chat_request.is_internal=False wins (even with internal_ prefix)
- is_internal=None falls back to detecting the legacy 'internal_' prefix
  on request_source
- is_internal=None with no internal_ prefix yields False

Plus a smoke check that the rest of the recorder state is built correctly.
"""

from unittest.mock import MagicMock, patch

import pytest

# Importing server is heavy (initializes dal/config), so do it lazily inside a
# fixture. The test still exercises the real function.


@pytest.fixture(scope="module")
def build_chat_recorder_state():
    # Patch the module-level dal in server.py before importing so init doesn't
    # try to authenticate against a real Supabase URL.
    with patch("holmes.core.supabase_dal.create_client"):
        from server import _build_chat_recorder_state
    return _build_chat_recorder_state


def _make_request_ai(model="openai/gpt-4", is_robusta=False):
    ai = MagicMock()
    ai.llm = MagicMock()
    ai.llm.model = model
    ai.llm.is_robusta_model = is_robusta
    return ai


def _chat_request(**overrides):
    """Build a minimal ChatRequest. Imports lazily so server-import side
    effects happen only once via the fixture."""
    from holmes.core.models import ChatRequest

    base = dict(ask="test question", stream=False)
    base.update(overrides)
    return ChatRequest(**base)


class TestIsInternalDerivation:
    def test_explicit_true_wins(self, build_chat_recorder_state):
        req = _chat_request(is_internal=True, request_source="freeform")
        state = build_chat_recorder_state(req, _make_request_ai(), is_streaming=False)
        assert state.is_internal is True

    def test_explicit_false_wins_even_with_internal_prefix(
        self, build_chat_recorder_state
    ):
        # FE may have a "freeform" request labeled with an internal_-prefixed
        # request_source for some reason; the explicit False should still win.
        req = _chat_request(is_internal=False, request_source="internal_legacy_user_chat")
        state = build_chat_recorder_state(req, _make_request_ai(), is_streaming=False)
        assert state.is_internal is False

    def test_unset_falls_back_to_internal_prefix(self, build_chat_recorder_state):
        # Backwards-compat: existing FE clients use the internal_ prefix
        # convention without setting is_internal explicitly.
        req = _chat_request(request_source="internal_title_generation")
        state = build_chat_recorder_state(req, _make_request_ai(), is_streaming=False)
        assert state.is_internal is True

    def test_unset_with_no_prefix_yields_false(self, build_chat_recorder_state):
        req = _chat_request(request_source="freeform")
        state = build_chat_recorder_state(req, _make_request_ai(), is_streaming=False)
        assert state.is_internal is False

    def test_unset_with_no_request_source_yields_false(self, build_chat_recorder_state):
        # No FE labeling at all → not internal.
        req = _chat_request()
        state = build_chat_recorder_state(req, _make_request_ai(), is_streaming=False)
        assert state.is_internal is False


class TestRecorderStateSmoke:
    """Catch obvious wiring regressions in _build_chat_recorder_state."""

    def test_carries_through_basic_fields(self, build_chat_recorder_state):
        req = _chat_request(
            user_id="u-abc",
            conversation_id="conv-123",
            request_source="alert_investigation",
            source_ref="issue-42",
            meta={"experiment_id": "x"},
        )
        state = build_chat_recorder_state(
            req, _make_request_ai(model="anthropic/claude-sonnet-4-5"), is_streaming=True
        )

        assert state.request_type == "user_chat"  # default
        assert state.request_source == "alert_investigation"
        assert state.source_ref == "issue-42"
        assert state.conversation_id == "conv-123"
        # Default for direct /api/chat: chat_history when conversation_id is set.
        assert state.conversation_source == "chat_history"
        assert state.user_id == "u-abc"
        assert state.is_streaming is True
        assert state.is_internal is False
        assert state.model == "anthropic/claude-sonnet-4-5"
        assert state.meta == {"experiment_id": "x"}
