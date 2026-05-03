"""Unit tests for holmes.core.usage_recorder.

The recorder is fire-and-forget — it spawns a daemon thread to write the row.
For deterministic tests we patch threading.Thread so the target runs inline,
which lets us assert against the exact kwargs passed to dal.record_usage_event.
"""

from typing import List
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.usage_recorder import (
    UsageRecorderState,
    record_error,
    record_from_llm_result,
    stream_with_usage_recording,
)
from holmes.utils.stream import StreamEvents, StreamMessage


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_state(**overrides) -> UsageRecorderState:
    """Build a UsageRecorderState with sensible defaults for tests."""
    base = dict(
        dal=MagicMock(enabled=True),
        request_type="user_chat",
        model="openai/gpt-4",
        provider="openai",
        is_robusta_model=False,
        request_source="freeform",
        source_ref=None,
        conversation_id="conv-123",
        conversation_source="chat_history",
        user_id="user-abc",
        is_streaming=True,
    )
    base.update(overrides)
    return UsageRecorderState(**base)


def _stream(*events: StreamMessage):
    for e in events:
        yield e


def _terminal_data(costs: dict, num_llm_calls: int = 1, finish_reason: str = "stop") -> dict:
    return {
        "content": "ok",
        "messages": [],
        "metadata": {"costs": costs, "finish_reason": finish_reason},
        "num_llm_calls": num_llm_calls,
        "costs": costs,
    }


def _patch_inline_thread(monkeypatch):
    """Replace threading.Thread inside usage_recorder so target() runs inline."""
    import holmes.core.usage_recorder as mod

    class _InlineThread:
        def __init__(self, target=None, kwargs=None, daemon=None, name=None):
            self._target = target
            self._kwargs = kwargs or {}

        def start(self):
            self._target(**self._kwargs)

    monkeypatch.setattr(mod.threading, "Thread", _InlineThread)


# ──────────────────────────────────────────────────────────────────
# UsageRecorderState.to_kwargs
# ──────────────────────────────────────────────────────────────────


class TestToKwargs:
    def test_packs_all_required_fields(self):
        state = _make_state()
        kwargs = state.to_kwargs()

        # Identity
        assert kwargs["request_type"] == "user_chat"
        assert kwargs["request_source"] == "freeform"
        assert kwargs["conversation_id"] == "conv-123"
        assert kwargs["conversation_source"] == "chat_history"
        assert kwargs["user_id"] == "user-abc"
        assert "request_id" in kwargs and kwargs["request_id"]

        # Classification
        assert kwargs["model"] == "openai/gpt-4"
        assert kwargs["provider"] == "openai"
        assert kwargs["is_robusta_model"] is False
        assert kwargs["is_streaming"] is True

        # Mutable defaults
        assert kwargs["status"] == "success"
        assert kwargs["iterations"] == 0
        assert kwargs["tool_call_count"] == 0
        assert kwargs["finish_reason"] is None
        assert kwargs["meta"] == {}

        # Stats default to an empty RequestStats, not None
        assert kwargs["stats"] is not None
        assert kwargs["stats"].total_tokens == 0
        assert kwargs["stats"].total_cost == 0.0

    def test_duration_ms_is_computed_from_t_start(self):
        state = _make_state()
        # Force t_start to be in the past so duration_ms > 0
        state.t_start -= 1.0
        kwargs = state.to_kwargs()
        assert kwargs["duration_ms"] >= 1000

    def test_is_internal_defaults_to_false(self):
        state = _make_state()
        assert state.is_internal is False
        assert state.to_kwargs()["is_internal"] is False

    def test_is_internal_true_round_trips(self):
        state = _make_state(is_internal=True)
        assert state.to_kwargs()["is_internal"] is True


# ──────────────────────────────────────────────────────────────────
# stream_with_usage_recording
# ──────────────────────────────────────────────────────────────────


class TestStreamWithUsageRecording:
    def test_success_path_records_with_status_success(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        costs = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "total_cost": 0.001}
        events = [
            StreamMessage(event=StreamEvents.START_TOOL, data={}),
            StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_name": "kubectl"}),
            StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_name": "prom"}),
            StreamMessage(event=StreamEvents.ANSWER_END, data=_terminal_data(costs, num_llm_calls=3)),
        ]

        # Drain the wrapped stream — the recorder fires in the finally block.
        consumed: List[StreamMessage] = list(
            stream_with_usage_recording(_stream(*events), state)
        )

        assert len(consumed) == 4
        state.dal.record_usage_event.assert_called_once()
        kw = state.dal.record_usage_event.call_args.kwargs
        assert kw["status"] == "success"
        assert kw["tool_call_count"] == 2
        assert kw["iterations"] == 3
        assert kw["finish_reason"] == "stop"
        assert kw["stats"].prompt_tokens == 100
        assert kw["stats"].total_tokens == 150

    def test_error_event_marks_status_error(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        events = [
            StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_name": "k"}),
            StreamMessage(event=StreamEvents.ERROR, data={"metadata": {}}),
        ]
        list(stream_with_usage_recording(_stream(*events), state))

        kw = state.dal.record_usage_event.call_args.kwargs
        assert kw["status"] == "error"
        assert kw["tool_call_count"] == 1

    def test_approval_required_marks_status_approval_required(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        list(stream_with_usage_recording(
            _stream(StreamMessage(event=StreamEvents.APPROVAL_REQUIRED, data={"metadata": {}})),
            state,
        ))
        assert state.dal.record_usage_event.call_args.kwargs["status"] == "approval_required"

    def test_exception_in_inner_stream_still_fires_recorder_with_error_status(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        def failing_stream():
            yield StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_name": "x"})
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            list(stream_with_usage_recording(failing_stream(), state))

        # Recorder still fires from the finally
        state.dal.record_usage_event.assert_called_once()
        assert state.dal.record_usage_event.call_args.kwargs["status"] == "error"
        # And the tool we saw before the exception was counted
        assert state.dal.record_usage_event.call_args.kwargs["tool_call_count"] == 1

    def test_stream_without_terminal_event_still_records_as_aborted(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        # No terminal — e.g. client disconnected.
        list(stream_with_usage_recording(
            _stream(StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_name": "y"})),
            state,
        ))

        # finally block still fired, but status downgraded from default
        # "success" to "aborted" because no terminal event was seen.
        state.dal.record_usage_event.assert_called_once()
        assert state.dal.record_usage_event.call_args.kwargs["status"] == "aborted"

    def test_terminal_event_keeps_its_explicit_status(self, monkeypatch):
        """Sanity: the abort downgrade only applies when no terminal was seen."""
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        list(stream_with_usage_recording(
            _stream(StreamMessage(
                event=StreamEvents.ANSWER_END,
                data={"metadata": {}, "num_llm_calls": 1},
            )),
            state,
        ))

        assert state.dal.record_usage_event.call_args.kwargs["status"] == "success"

    def test_request_id_injected_into_answer_end_metadata(self, monkeypatch):
        """The FE needs request_id from ai_answer_end so it can post feedback later."""
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        state.request_id = "deadbeef-1234"

        answer_end = StreamMessage(
            event=StreamEvents.ANSWER_END,
            data={
                "content": "ok",
                "messages": [],
                "metadata": {"costs": {"total_tokens": 100}},
                "num_llm_calls": 1,
            },
        )
        consumed = list(stream_with_usage_recording(_stream(answer_end), state))

        # The same StreamMessage flows through; its metadata now has request_id.
        out = consumed[0]
        assert out.event == StreamEvents.ANSWER_END
        assert out.data["metadata"]["request_id"] == "deadbeef-1234"
        # Existing metadata content (costs) is preserved alongside.
        assert out.data["metadata"]["costs"] == {"total_tokens": 100}

    def test_request_id_injected_when_metadata_missing(self, monkeypatch):
        """If the upstream event has no metadata key, _inject_request_id creates it."""
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        state.request_id = "uuid-xyz"

        answer_end = StreamMessage(
            event=StreamEvents.ANSWER_END,
            data={"content": "ok", "messages": [], "num_llm_calls": 1},  # no metadata key
        )
        consumed = list(stream_with_usage_recording(_stream(answer_end), state))
        assert consumed[0].data["metadata"] == {"request_id": "uuid-xyz"}

    def test_request_id_injected_into_approval_required(self, monkeypatch):
        """Feedback should be possible on paused turns too — request_id must be there."""
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        state.request_id = "rid-paused"

        approval = StreamMessage(
            event=StreamEvents.APPROVAL_REQUIRED,
            data={"metadata": {}, "pending_approvals": []},
        )
        consumed = list(stream_with_usage_recording(_stream(approval), state))
        assert consumed[0].data["metadata"]["request_id"] == "rid-paused"

    def test_request_id_injected_into_error_event(self, monkeypatch):
        """Surface request_id even on ERROR so the FE can report 'this request failed'."""
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        state.request_id = "rid-err"

        err = StreamMessage(event=StreamEvents.ERROR, data={"metadata": {}})
        consumed = list(stream_with_usage_recording(_stream(err), state))
        assert consumed[0].data["metadata"]["request_id"] == "rid-err"


# ──────────────────────────────────────────────────────────────────
# record_from_llm_result (non-streaming)
# ──────────────────────────────────────────────────────────────────


class TestRecordFromLlmResult:
    def test_extracts_stats_iterations_finish_reason_and_tool_count(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state(is_streaming=False)

        # Build a fake LLMResult-shaped object. RequestStats fields are inherited,
        # so we can dump a flat dict via model_dump() in the helper.
        fake_result = MagicMock()
        fake_result.model_dump.return_value = {
            "total_cost": 0.005,
            "total_tokens": 250,
            "prompt_tokens": 200,
            "completion_tokens": 50,
            "cached_tokens": None,
            "reasoning_tokens": 0,
            "max_completion_tokens_per_call": 50,
            "max_prompt_tokens_per_call": 200,
            "num_compactions": 0,
        }
        fake_result.num_llm_calls = 4
        fake_result.tool_calls = [object(), object(), object()]
        fake_result.finish_reason = "stop"

        record_from_llm_result(state, fake_result)

        kw = state.dal.record_usage_event.call_args.kwargs
        assert kw["status"] == "success"
        assert kw["iterations"] == 4
        assert kw["tool_call_count"] == 3
        assert kw["finish_reason"] == "stop"
        assert kw["stats"].total_tokens == 250
        assert kw["stats"].total_cost == 0.005

    def test_handles_missing_attrs_gracefully(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        # An LLMResult-like object with only the bare minimum
        bare = MagicMock()
        bare.model_dump.return_value = {}
        bare.num_llm_calls = None
        bare.tool_calls = None
        bare.finish_reason = None

        record_from_llm_result(state, bare)

        kw = state.dal.record_usage_event.call_args.kwargs
        # iterations falls back to 1 when num_llm_calls is None
        assert kw["iterations"] == 1
        assert kw["tool_call_count"] == 0


# ──────────────────────────────────────────────────────────────────
# record_error
# ──────────────────────────────────────────────────────────────────


class TestRecordError:
    def test_marks_rate_limited_when_rate_limit_in_message(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        record_error(state, RuntimeError("rate limit exceeded for model"))
        kw = state.dal.record_usage_event.call_args.kwargs
        assert kw["status"] == "rate_limited"

    def test_marks_error_for_other_exceptions(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        record_error(state, ValueError("invalid model"))
        kw = state.dal.record_usage_event.call_args.kwargs
        assert kw["status"] == "error"


# ──────────────────────────────────────────────────────────────────
# Disabled-DAL no-op behavior
# ──────────────────────────────────────────────────────────────────


class TestDisabledDalNoop:
    def test_no_thread_spawned_when_dal_disabled(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state(dal=MagicMock(enabled=False))
        record_error(state, RuntimeError("anything"))
        # The disabled DAL has the method mocked but should never be called.
        state.dal.record_usage_event.assert_not_called()

    def test_no_thread_spawned_when_dal_is_none(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state(dal=None)
        # No exception; nothing happens.
        record_error(state, RuntimeError("x"))


# ──────────────────────────────────────────────────────────────────
# Real Thread mode (no inline patching) — verifies fire-and-forget
# ──────────────────────────────────────────────────────────────────


class TestFireAndForgetThreadMode:
    def test_record_calls_dal_in_background_thread(self):
        import threading
        import time

        called = threading.Event()

        def slow_record(**kwargs):
            time.sleep(0.05)
            called.set()

        dal = MagicMock(enabled=True)
        dal.record_usage_event = slow_record
        state = _make_state(dal=dal)
        record_error(state, RuntimeError("x"))
        # Caller returns immediately; the thread is still running.
        # Wait briefly for it to finish.
        assert called.wait(timeout=2.0), "background thread did not run record_usage_event"

    def test_dal_exception_does_not_propagate(self, monkeypatch):
        # _fire wraps Thread.start() in a try/except so even with inline-thread
        # patching (where target runs synchronously in start()) downstream
        # exceptions don't bubble out to the caller. Logged via
        # logging.exception(), but the caller is unaffected. This is the
        # defense-in-depth contract — telemetry must never break the response.
        _patch_inline_thread(monkeypatch)

        dal = MagicMock(enabled=True)
        dal.record_usage_event.side_effect = RuntimeError("supabase down")
        state = _make_state(dal=dal)

        # Should NOT raise. The inner try/except in _fire swallows it.
        record_error(state, RuntimeError("x"))
