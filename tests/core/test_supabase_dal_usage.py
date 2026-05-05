"""Unit tests for SupabaseDal.record_usage_event and SupabaseDal.record_feedback.

These methods are best-effort: they swallow Supabase errors so the response
path can never be broken by a telemetry write. The tests verify both the
happy path (correct payload sent to .insert / .update) and the failure path
(exceptions are absorbed).
"""

from unittest.mock import MagicMock, patch

import pytest

from holmes.core.llm_usage import RequestStats
from holmes.core.supabase_dal import (
    HOLMES_USAGE_EVENTS_TABLE,
    SupabaseDal,
)


@pytest.fixture
def mock_dal():
    """A SupabaseDal with mocked Supabase client and account_id."""
    with patch("holmes.core.supabase_dal.create_client"):
        dal = SupabaseDal(cluster="test-cluster")
        dal.enabled = True
        dal.account_id = "00000000-0000-0000-0000-000000000001"
        dal.cluster = "test-cluster"
        dal.client = MagicMock()
        return dal


def _stats() -> RequestStats:
    return RequestStats(
        total_cost=0.0123,
        total_tokens=1234,
        prompt_tokens=1000,
        completion_tokens=234,
        cached_tokens=50,
        reasoning_tokens=12,
        max_completion_tokens_per_call=234,
        max_prompt_tokens_per_call=1000,
        num_compactions=1,
    )


# ──────────────────────────────────────────────────────────────────
# record_usage_event
# ──────────────────────────────────────────────────────────────────


class TestRecordUsageEvent:
    def test_no_op_when_dal_disabled(self, mock_dal):
        mock_dal.enabled = False
        mock_dal.record_usage_event(
            request_type="user_chat",
            request_source=None,
            source_ref=None,
            conversation_id=None,
            conversation_source=None,
            status="success",
            model="openai/gpt-4",
            provider="openai",
            is_robusta_model=False,
            stats=_stats(),
            iterations=1,
            duration_ms=42,
            tool_call_count=0,
            is_streaming=False,
            finish_reason=None,
            user_id=None,
        )
        # No client interaction at all when disabled.
        mock_dal.client.table.assert_not_called()

    def test_inserts_row_with_correct_payload(self, mock_dal):
        mock_dal.record_usage_event(
            request_type="user_chat",
            request_source="freeform",
            source_ref="issue-42",
            conversation_id="conv-abc",
            conversation_source="chat_history",
            status="success",
            model="anthropic/claude-sonnet-4-5",
            provider="anthropic",
            is_robusta_model=False,
            stats=_stats(),
            iterations=3,
            duration_ms=1500,
            tool_call_count=5,
            is_streaming=True,
            finish_reason="stop",
            user_id="user-xyz",
            request_id="req-uuid-123",
            meta={"experiment_id": "abc"},
        )

        # client.table(<table>).insert(<payload>).execute()
        mock_dal.client.table.assert_called_once_with(HOLMES_USAGE_EVENTS_TABLE)
        insert_call = mock_dal.client.table.return_value.insert
        insert_call.assert_called_once()

        payload = insert_call.call_args.args[0]

        # Identity
        assert payload["account_id"] == mock_dal.account_id
        assert payload["cluster_id"] == "test-cluster"
        assert payload["user_id"] == "user-xyz"
        assert payload["conversation_id"] == "conv-abc"
        assert payload["conversation_source"] == "chat_history"
        assert payload["request_id"] == "req-uuid-123"

        # Classification
        assert payload["request_type"] == "user_chat"
        assert payload["request_source"] == "freeform"
        assert payload["source_ref"] == "issue-42"
        assert payload["status"] == "success"
        assert payload["model"] == "anthropic/claude-sonnet-4-5"
        assert payload["provider"] == "anthropic"
        assert payload["is_robusta_model"] is False

        # Stats
        assert payload["prompt_tokens"] == 1000
        assert payload["completion_tokens"] == 234
        assert payload["cached_tokens"] == 50
        assert payload["reasoning_tokens"] == 12
        assert payload["total_tokens"] == 1234
        assert payload["total_cost"] == pytest.approx(0.0123)
        assert payload["num_compactions"] == 1
        assert payload["iterations"] == 3
        assert payload["max_prompt_tokens_per_call"] == 1000
        assert payload["max_completion_tokens_per_call"] == 234

        # Outcome
        assert payload["tool_call_count"] == 5
        assert payload["duration_ms"] == 1500
        assert payload["is_streaming"] is True
        assert payload["finish_reason"] == "stop"
        assert payload["meta"] == {"experiment_id": "abc"}

    def test_falls_back_to_dal_cluster_when_cluster_id_not_supplied(self, mock_dal):
        mock_dal.record_usage_event(
            request_type="user_chat",
            request_source=None,
            source_ref=None,
            conversation_id=None,
            conversation_source=None,
            status="success",
            model="m",
            provider="p",
            is_robusta_model=False,
            stats=_stats(),
            iterations=1,
            duration_ms=10,
            tool_call_count=0,
            is_streaming=False,
            finish_reason=None,
            user_id=None,
        )
        payload = mock_dal.client.table.return_value.insert.call_args.args[0]
        assert payload["cluster_id"] == "test-cluster"

    def test_explicit_cluster_id_overrides_dal_default(self, mock_dal):
        mock_dal.record_usage_event(
            request_type="user_chat",
            request_source=None,
            source_ref=None,
            conversation_id=None,
            conversation_source=None,
            status="success",
            model="m",
            provider="p",
            is_robusta_model=False,
            stats=_stats(),
            iterations=1,
            duration_ms=10,
            tool_call_count=0,
            is_streaming=False,
            finish_reason=None,
            user_id=None,
            cluster_id="other-cluster",
        )
        payload = mock_dal.client.table.return_value.insert.call_args.args[0]
        assert payload["cluster_id"] == "other-cluster"

    def test_meta_defaults_to_empty_dict_when_none(self, mock_dal):
        mock_dal.record_usage_event(
            request_type="user_chat",
            request_source=None,
            source_ref=None,
            conversation_id=None,
            conversation_source=None,
            status="success",
            model="m",
            provider="p",
            is_robusta_model=False,
            stats=_stats(),
            iterations=1,
            duration_ms=10,
            tool_call_count=0,
            is_streaming=False,
            finish_reason=None,
            user_id=None,
            meta=None,
        )
        payload = mock_dal.client.table.return_value.insert.call_args.args[0]
        assert payload["meta"] == {}

    def test_swallows_supabase_errors(self, mock_dal):
        # Supabase client raises — record_usage_event must not bubble up.
        mock_dal.client.table.return_value.insert.return_value.execute.side_effect = (
            RuntimeError("supabase down")
        )
        # Should not raise.
        mock_dal.record_usage_event(
            request_type="user_chat",
            request_source=None,
            source_ref=None,
            conversation_id=None,
            conversation_source=None,
            status="success",
            model="m",
            provider="p",
            is_robusta_model=False,
            stats=_stats(),
            iterations=1,
            duration_ms=10,
            tool_call_count=0,
            is_streaming=False,
            finish_reason=None,
            user_id=None,
        )

    def test_handles_stats_with_none_cached_tokens(self, mock_dal):
        # Some providers don't report cached_tokens — should land as NULL.
        stats = RequestStats(
            total_cost=0.001,
            total_tokens=100,
            prompt_tokens=80,
            completion_tokens=20,
            cached_tokens=None,
            reasoning_tokens=0,
        )
        mock_dal.record_usage_event(
            request_type="user_chat",
            request_source=None,
            source_ref=None,
            conversation_id=None,
            conversation_source=None,
            status="success",
            model="m",
            provider="p",
            is_robusta_model=False,
            stats=stats,
            iterations=1,
            duration_ms=10,
            tool_call_count=0,
            is_streaming=False,
            finish_reason=None,
            user_id=None,
        )
        payload = mock_dal.client.table.return_value.insert.call_args.args[0]
        assert payload["cached_tokens"] is None


# ──────────────────────────────────────────────────────────────────
# record_feedback
# ──────────────────────────────────────────────────────────────────


class TestRecordFeedback:
    def test_no_op_when_dal_disabled(self, mock_dal):
        mock_dal.enabled = False
        mock_dal.record_feedback(
            request_id="r-1",
            sentiment="thumbs_up",
            category=None,
            comment=None,
            user_id=None,
        )
        mock_dal.client.table.assert_not_called()

    def test_invalid_sentiment_logs_and_returns(self, mock_dal):
        # Should not raise, should not call .update.
        mock_dal.record_feedback(
            request_id="r-1",
            sentiment="meh",  # not allowed
            category=None,
            comment=None,
            user_id=None,
        )
        mock_dal.client.table.assert_not_called()

    def test_updates_correct_columns_with_account_and_request_id_filter(self, mock_dal):
        mock_dal.record_feedback(
            request_id="req-uuid-123",
            sentiment="thumbs_down",
            category="wrong_answer",
            comment="missed the OOM",
            user_id="user-xyz",
        )

        # Chain: client.table(...).update({...}).eq("account_id", ...).eq("request_id", ...).execute()
        mock_dal.client.table.assert_called_once_with(HOLMES_USAGE_EVENTS_TABLE)
        update_mock = mock_dal.client.table.return_value.update
        update_mock.assert_called_once()
        update_payload = update_mock.call_args.args[0]
        assert update_payload["feedback_sentiment"] == "thumbs_down"
        assert update_payload["feedback_category"] == "wrong_answer"
        assert update_payload["feedback_comment"] == "missed the OOM"
        # feedback_at must be a UTC ISO timestamp — without an explicit
        # timezone, Postgres timestamptz interprets the value relative to
        # the *server*'s local time which differs from the Holmes pod's,
        # producing shifted timestamps. Assert a UTC offset is present.
        feedback_at = update_payload["feedback_at"]
        assert isinstance(feedback_at, str)
        # Python's datetime.isoformat() with timezone.utc produces
        # '2026-01-02T03:04:05.678+00:00' — accept either '+00:00' or 'Z'.
        assert feedback_at.endswith("+00:00") or feedback_at.endswith("Z"), (
            f"feedback_at must include a UTC offset, got {feedback_at!r}"
        )

        # Account-scoped + request_id WHERE clauses
        eq_calls = update_mock.return_value.eq.call_args_list
        # First .eq is account_id, second is request_id (chained on the return value)
        assert eq_calls[0].args == ("account_id", mock_dal.account_id)
        # The second .eq is on the result of the first, but with our MagicMock chain
        # it's also captured here. In any case, both account_id and request_id
        # must be in the predicate chain.
        first_eq_return = update_mock.return_value.eq.return_value
        first_eq_return.eq.assert_called_with("request_id", "req-uuid-123")

    def test_swallows_supabase_errors(self, mock_dal):
        mock_dal.client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.side_effect = (
            RuntimeError("update failed")
        )
        # Should not raise.
        mock_dal.record_feedback(
            request_id="r-1",
            sentiment="thumbs_up",
            category=None,
            comment=None,
            user_id=None,
        )
