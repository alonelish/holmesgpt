from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from holmes.core.models import ToolCallResult


@dataclass
class IterationStartEvent:
    """Emitted at the beginning of each loop iteration."""

    iteration: int
    max_steps: int


@dataclass
class CompactionEvent:
    """Emitted when the context window limiter compacts conversation history."""

    metadata: Dict[str, Any]
    # The StreamMessage events from the limiter (for backwards compat with call_stream)
    stream_events: list = field(default_factory=list)


@dataclass
class LLMResponseEvent:
    """Emitted after the LLM returns a response."""

    content: Optional[str]
    reasoning_content: Optional[str]
    has_tool_calls: bool
    iteration: int


@dataclass
class ToolStartEvent:
    """Emitted before a tool begins execution."""

    tool_call_id: str
    tool_name: str
    tool_number: Optional[int]


@dataclass
class ToolResultEvent:
    """Emitted after a tool finishes execution."""

    tool_call_result: ToolCallResult


@dataclass
class TokenCountEvent:
    """Emitted after token counts are computed."""

    metadata: Dict[str, Any]


@dataclass
class CompletionEvent:
    """Emitted when the loop finishes (no more tool calls)."""

    result: Optional[str]
    messages: List[Dict]
    tool_calls: List[Any]
    metadata: Dict[str, Any]
    num_llm_calls: int


@dataclass
class ApprovalRequiredEvent:
    """Emitted when tools require user approval (streaming mode)."""

    pending_approvals: list  # list[PendingToolApproval]
    approval_required_tools: list  # list[ToolCallResult]
    messages: List[Dict]
    metadata: Dict[str, Any]
    tool_number_offset: int = 0


AgentEvent = Union[
    IterationStartEvent,
    CompactionEvent,
    LLMResponseEvent,
    ToolStartEvent,
    ToolResultEvent,
    TokenCountEvent,
    CompletionEvent,
    ApprovalRequiredEvent,
]
