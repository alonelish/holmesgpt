"""Shared helper for recording AI usage events to HolmesUsageEvents.

Used by every LLM-consuming entry point (server.py /api/chat, the
ConversationWorker, scheduled prompts, the AG-UI server, and
holmes/checks/checks_api.py) so usage tracking is consistent and there's
exactly one place to update if the recording shape changes.

The recorder is fire-and-forget: each call spawns a daemon thread to do
the DB write. Telemetry must never block or break the response path.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Generator, Optional

from holmes.core.llm_usage import RequestStats
from holmes.utils.stream import StreamEvents, StreamMessage

if TYPE_CHECKING:
    from holmes.core.models import ChatRequest
    from holmes.core.supabase_dal import SupabaseDal
    from holmes.core.tool_calling_llm import LLMResult


# Slack auto-detection: the Robusta runner's Slack handler currently prepends
# a fixed prefix to the user's message before POSTing /api/chat. Example:
#   "**@user_U0AKMP2CZ97** • 2026-05-04T05:10:04Z\n\nhigh cpu in pod alert"
# Extracted into a shared regex so both the direct /api/chat path (server.py)
# and the worker path (conversations_worker/worker.py) can run the same
# detection. Heuristic — fragile if the runner format changes.
_SLACK_ASK_PREFIX_RE = re.compile(
    r"^\*\*@user_(?P<slack_user_id>U[A-Z0-9]+)\*\*\s*•\s*"
    r"(?P<slack_triggered_at>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)",
)


def detect_slack_origin(ask: Optional[str]) -> Optional[Dict[str, Any]]:
    """If `ask` matches the runner's Slack-prefix shape, return parsed
    metadata (slack_user_id + slack_triggered_at). Otherwise None.
    """
    if not ask:
        return None
    m = _SLACK_ASK_PREFIX_RE.match(ask)
    if not m:
        return None
    return {
        "slack_user_id": m.group("slack_user_id"),
        "slack_triggered_at": m.group("slack_triggered_at"),
    }


def resolve_provider(model: Optional[str]) -> str:
    """Best-effort: return the canonical litellm provider for `model`.

    Falls back to splitting on the litellm prefix (`openai/...`,
    `anthropic/...`) if the helper raises (e.g. for unrecognized models).
    Importing litellm is deferred so this module stays import-light for
    consumers that only want the dataclass.
    """
    if not model:
        return "unknown"
    try:
        import litellm  # local import — keep usage_recorder cheap to import
        return litellm.get_llm_provider(model)[1] or "unknown"
    except Exception:
        return model.split("/")[0] if "/" in model else "unknown"


def build_chat_recorder_state(
    chat_request: "ChatRequest",
    request_ai: Any,
    *,
    dal: Any,
    is_streaming: bool,
) -> "UsageRecorderState":
    """Construct a UsageRecorderState from a ChatRequest.

    Used by every code path that consumes a ChatRequest and wraps a stream:
    server.py:chat() (direct /api/chat) and ConversationWorker._run_chat_and_publish
    (worker path). Centralizes the request_type / request_source / is_internal
    / Slack auto-detection logic so all entry points get identical behavior.
    """
    # Default conversation_source to 'chat_history' when conversation_id is set
    # but the caller didn't override (i.e. direct /api/chat). The worker passes
    # 'conversations' explicitly.
    conversation_source = chat_request.conversation_source
    if conversation_source is None and chat_request.conversation_id:
        conversation_source = "chat_history"

    model_name = (
        getattr(request_ai.llm, "model", None)
        or chat_request.model
        or "unknown"
    )

    # Internal calls (title generation, classification, summarization, etc.)
    # get filtered out of user-facing dashboards. FE sets is_internal=True
    # explicitly for those. Backwards compat: if FE didn't set it, fall back
    # to detecting the legacy 'internal_' prefix on request_source.
    if chat_request.is_internal is None:
        is_internal = bool(
            chat_request.request_source
            and chat_request.request_source.startswith("internal_")
        )
    else:
        is_internal = bool(chat_request.is_internal)

    # Slack auto-detection: tag both request_type='slack_chat' and
    # request_source='slack' as defaults that the caller can still override.
    slack_info = detect_slack_origin(chat_request.ask)
    if chat_request.request_type:
        request_type = chat_request.request_type
    elif slack_info is not None:
        request_type = "slack_chat"
    else:
        request_type = "user_chat"

    request_source = chat_request.request_source
    if request_source is None and slack_info is not None:
        request_source = "slack"

    # Merge meta: FE-supplied keys, then backend-derived keys (backend wins
    # on collision). Slack info goes under a 'slack' sub-key so it doesn't
    # clutter the top level.
    merged_meta: Dict[str, Any] = dict(chat_request.meta or {})
    if slack_info is not None:
        merged_meta["slack"] = slack_info

    return UsageRecorderState(
        dal=dal,
        request_type=request_type,
        request_source=request_source,
        source_ref=chat_request.source_ref,
        conversation_id=chat_request.conversation_id,
        conversation_source=conversation_source,
        user_id=chat_request.user_id,
        is_streaming=is_streaming,
        is_internal=is_internal,
        model=model_name,
        provider=resolve_provider(model_name),
        is_robusta_model=getattr(request_ai.llm, "is_robusta_model", False),
        meta=merged_meta,
    )


@dataclass
class UsageRecorderState:
    """All the data needed to write one HolmesUsageEvents row.

    Identity / classification fields are set by the entry point at
    construction time. Mutable runtime fields (`stats`, `iterations`,
    `tool_call_count`, `finish_reason`, `status`) are filled by the
    stream wrapper or by `record_from_llm_result` before firing.
    """

    # required identity / classification — set by the entry point
    dal: Any  # SupabaseDal; typed Any to avoid circular import at runtime
    request_type: str
    model: str
    provider: str
    is_robusta_model: bool

    # optional identity / classification
    request_source: Optional[str] = None
    source_ref: Optional[str] = None
    conversation_id: Optional[str] = None
    conversation_source: Optional[str] = None
    user_id: Optional[str] = None
    cluster_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    is_streaming: bool = False
    is_internal: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)

    # mutable — filled during the call by the wrapper or recorder
    t_start: float = field(default_factory=time.monotonic)
    stats: Optional[RequestStats] = None
    iterations: int = 0
    tool_call_count: int = 0
    finish_reason: Optional[str] = None
    status: str = "success"

    def to_kwargs(self) -> Dict[str, Any]:
        """Pack the state into the kwargs `SupabaseDal.record_usage_event` expects."""
        return {
            "request_type": self.request_type,
            "request_source": self.request_source,
            "source_ref": self.source_ref,
            "conversation_id": self.conversation_id,
            "conversation_source": self.conversation_source,
            "status": self.status,
            "model": self.model,
            "provider": self.provider,
            "is_robusta_model": self.is_robusta_model,
            "stats": self.stats or RequestStats(),
            "iterations": self.iterations,
            "duration_ms": int((time.monotonic() - self.t_start) * 1000),
            "tool_call_count": self.tool_call_count,
            "is_streaming": self.is_streaming,
            "is_internal": self.is_internal,
            "finish_reason": self.finish_reason,
            "user_id": self.user_id,
            "cluster_id": self.cluster_id,
            "request_id": self.request_id,
            "meta": self.meta,
        }


def stream_with_usage_recording(
    stream: Generator[StreamMessage, None, None],
    state: UsageRecorderState,
) -> Generator[StreamMessage, None, None]:
    """Forward stream events; capture state; record on stream end.

    Used by chat() and AG-UI. Watches for terminal events (ANSWER_END,
    APPROVAL_REQUIRED, ERROR) to extract final stats / counts / reason,
    counts TOOL_RESULT events along the way, and fires the recorder in
    a `finally` block so the row is written even on exceptions or
    client disconnects.

    Also injects ``state.request_id`` into the terminal event's
    ``metadata`` dict so the SSE formatter ships it back to the FE. The
    FE saves it from ``ai_answer_end`` and passes it to the
    ``public.record_feedback()`` Supabase RPC when the user clicks
    thumbs up/down.
    """
    saw_terminal = False
    try:
        for msg in stream:
            if msg.event == StreamEvents.TOOL_RESULT:
                state.tool_call_count += 1
            elif msg.event == StreamEvents.ANSWER_END:
                _capture_terminal(state, msg.data)
                _inject_request_id(msg.data, state.request_id)
                state.status = "success"
                saw_terminal = True
            elif msg.event == StreamEvents.APPROVAL_REQUIRED:
                _capture_terminal(state, msg.data)
                _inject_request_id(msg.data, state.request_id)
                state.status = "approval_required"
                saw_terminal = True
            elif msg.event == StreamEvents.ERROR:
                _capture_terminal(state, msg.data)
                _inject_request_id(msg.data, state.request_id)
                state.status = "error"
                saw_terminal = True
            yield msg
    except Exception:
        if not saw_terminal:
            state.status = "error"
        raise
    finally:
        # If the inner stream ended without yielding any terminal event
        # (client disconnected mid-stream, generator exhausted abnormally),
        # `state.status` would still be the constructor default "success".
        # That's wrong — mark such cases as "aborted" so dashboards can
        # filter incomplete runs out of "successful chat" metrics.
        if not saw_terminal and state.status == "success":
            state.status = "aborted"
        _fire(state)


def _inject_request_id(data: Dict[str, Any], request_id: str) -> None:
    """Drop request_id into data['metadata'] so the SSE formatter ships it
    to the FE. Creates the metadata dict if missing or non-dict-shaped.
    """
    md = data.get("metadata")
    if not isinstance(md, dict):
        md = {}
        data["metadata"] = md
    md["request_id"] = request_id


def _capture_terminal(state: UsageRecorderState, data: Dict[str, Any]) -> None:
    """Pull cost/iterations/finish_reason from a terminal event's data."""
    metadata = data.get("metadata") or {}
    costs = metadata.get("costs") or {}
    if costs:
        try:
            state.stats = RequestStats(**costs)
        except Exception:
            logging.debug(
                "Failed to materialize RequestStats from terminal event costs",
                exc_info=True,
            )
    # Explicit None-check rather than `or` so a legitimate 0 (unlikely but
    # not impossible) is preserved instead of falling back to state.iterations.
    raw_iterations = data.get("num_llm_calls")
    if raw_iterations is not None:
        state.iterations = raw_iterations
    state.finish_reason = (
        metadata.get("finish_reason") or state.finish_reason
    )


def record_from_llm_result(
    state: UsageRecorderState,
    llm_result: "LLMResult",
) -> None:
    """Record a usage event from a non-streaming `ai.call(...)` result.

    Used by `holmes/checks/checks.py:execute_check` and any other caller
    that gets back an LLMResult directly. LLMResult IS-A RequestStats
    (it inherits the cost / token fields), so we copy them out via
    model_dump.
    """
    try:
        # LLMResult inherits from RequestStats, so dump the stats fields out.
        stats_fields = {
            k: v
            for k, v in llm_result.model_dump().items()
            if k
            in {
                "total_cost",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                "cached_tokens",
                "reasoning_tokens",
                "max_completion_tokens_per_call",
                "max_prompt_tokens_per_call",
                "num_compactions",
            }
        }
        state.stats = RequestStats(**stats_fields)
    except Exception:
        logging.debug("Failed to extract stats from LLMResult", exc_info=True)
        state.stats = RequestStats()

    state.iterations = getattr(llm_result, "num_llm_calls", None) or 1
    state.tool_call_count = len(getattr(llm_result, "tool_calls", None) or [])
    state.finish_reason = getattr(llm_result, "finish_reason", None)
    state.status = "success"
    _fire(state)


def record_error(state: UsageRecorderState, exc: Exception) -> None:
    """Record a failed call where an exception bubbled before getting a result."""
    msg = str(exc).lower()
    if "rate" in msg and "limit" in msg:
        state.status = "rate_limited"
    else:
        state.status = "error"
    _fire(state)


def _fire(state: UsageRecorderState) -> None:
    """Background-thread the dal write so the response path never blocks."""
    if state.dal is None or not getattr(state.dal, "enabled", False):
        return
    try:
        threading.Thread(
            target=state.dal.record_usage_event,
            kwargs=state.to_kwargs(),
            daemon=True,
            name="usage-recorder",
        ).start()
    except Exception:
        # Defense in depth — record_usage_event has its own try/except too.
        logging.exception("Failed to spawn usage recorder thread")


__all__ = [
    "UsageRecorderState",
    "build_chat_recorder_state",
    "detect_slack_origin",
    "record_error",
    "record_from_llm_result",
    "resolve_provider",
    "stream_with_usage_recording",
]
