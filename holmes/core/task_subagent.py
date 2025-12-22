import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from holmes.common.env_vars import (
    ENABLE_TASK_SUBAGENT,
    TASK_SUBAGENT_MAX_INPUT_CHARS,
    TASK_SUBAGENT_SUMMARY_MAX_CHARS,
)
from holmes.core.llm import LLM
from holmes.core.models import ToolCallResult
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.plugins.prompts import load_and_render_prompt


@dataclass
class TaskSubAgentConfig:
    enabled: bool = ENABLE_TASK_SUBAGENT if ENABLE_TASK_SUBAGENT is not None else True
    summary_max_chars: int = TASK_SUBAGENT_SUMMARY_MAX_CHARS
    max_input_chars: int = TASK_SUBAGENT_MAX_INPUT_CHARS
    max_steps: int = 8


class TaskSubAgent:
    """
    Task sub-agent that runs a fresh ToolCallingLLM instance (with all tools available)
    to digest large tool outputs without polluting the main conversation history.
    """

    def __init__(
        self,
        llm: LLM,
        tool_executor: ToolExecutor,
        max_steps: int,
        config: Optional[TaskSubAgentConfig] = None,
        enable_task_subagent: bool = True,
        subagent_factory: Optional[
            Callable[[LLM, ToolExecutor, int], Any]
        ] = None,  # returns ToolCallingLLM
    ) -> None:
        self.llm = llm
        self.tool_executor = tool_executor
        self.config = config or TaskSubAgentConfig()
        self.enable_task_subagent = enable_task_subagent
        self.subagent_factory = subagent_factory
        # Use main agent max_steps unless overridden in config
        self.max_steps = self.config.max_steps or max_steps

    def is_enabled(self) -> bool:
        return bool(self.enable_task_subagent and self.config.enabled)

    def _should_summarize(self, content: str) -> bool:
        if not self.is_enabled():
            return False
        return len(content) > self.config.summary_max_chars

    def _truncate_input(self, content: str) -> str:
        if len(content) <= self.config.max_input_chars:
            return content

        logging.info(
            "TaskSubAgent input exceeded max_input_chars (%s). Pre-truncating.",
            self.config.max_input_chars,
        )
        suffix = "\n\n[Input truncated before sub-agent processing]"
        return content[: self.config.max_input_chars - len(suffix)] + suffix

    def _create_subagent(self):
        if self.subagent_factory:
            return self.subagent_factory(self.llm, self.tool_executor, self.max_steps)

        # Lazy import to avoid circular dependency
        from holmes.core.tool_calling_llm import ToolCallingLLM

        return ToolCallingLLM(
            tool_executor=self.tool_executor,
            max_steps=self.max_steps,
            llm=self.llm,
            enable_task_subagent=False,  # prevent nested sub-agents
        )

    def summarize_tool_message(
        self,
        tool_call_result: ToolCallResult,
        original_message: Dict[str, Any],
        parent_messages: Optional[list[dict]] = None,
    ) -> tuple[Dict[str, Any], Optional[dict]]:
        """
        If the tool output is large, spin up a Task sub-agent (full tool access, fresh context)
        to research/condense the output and return a compact message.
        """
        content = original_message.get("content", "")
        if not isinstance(content, str) or not self._should_summarize(content):
            return original_message, None

        parent_context = ""
        if parent_messages:
            last_user_message = next(
                (
                    m.get("content", "")
                    for m in reversed(parent_messages)
                    if m.get("role") == "user"
                ),
                "",
            )
            parent_context = str(last_user_message)

        truncated_input = self._truncate_input(content)
        user_prompt = load_and_render_prompt(
            "builtin://task_subagent_summarize_tool.jinja2",
            {
                "tool_name": tool_call_result.tool_name,
                "tool_description": tool_call_result.description,
                "parent_user_message": parent_context,
                "tool_output": truncated_input,
                "summary_max_chars": self.config.summary_max_chars,
            },
        )

        subagent = self._create_subagent()
        try:
            subagent_result = subagent.prompt_call(
                system_prompt="You are a Task SubAgent with the same tools as the main agent. Investigate and summarize without spawning additional sub-agents.",
                user_prompt=user_prompt,
            )
            summary_text = subagent_result.result or ""
            tool_calls = subagent_result.tool_calls or []
        except Exception:
            logging.exception("TaskSubAgent failed to research/summarize tool output.")
            summary_text = ""
            tool_calls = []

        if not summary_text.strip():
            summary_text = truncated_input[: self.config.summary_max_chars]

        summarized_message = dict(original_message)
        summarized_message["content"] = (
            f"[Task SubAgent Summary for {tool_call_result.tool_name}]\n{summary_text}"
        )

        metadata = {
            "tool_call_id": tool_call_result.tool_call_id,
            "tool_name": tool_call_result.tool_name,
            "original_chars": len(content),
            "summary_chars": len(summary_text),
            "task_subagent_tool_calls": tool_calls,
        }

        return summarized_message, metadata
