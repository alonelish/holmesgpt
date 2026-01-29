import logging
import os
from typing import Any, Dict
from uuid import uuid4

from holmes.core.todo_tasks_formatter import format_tasks
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.investigator.model import Task, TaskStatus
from holmes.utils.console.task_display import display_tasks

TODO_WRITE_TOOL_NAME = "TodoWrite"


def parse_tasks(todos_data: Any) -> list[Task]:
    tasks = []

    for todo_item in todos_data:
        if isinstance(todo_item, dict):
            task = Task(
                id=todo_item.get("id", str(uuid4())),
                content=todo_item.get("content", ""),
                status=TaskStatus(todo_item.get("status", "pending")),
            )
            tasks.append(task)

    return tasks


class TodoWriteTool(Tool):
    name: str = TODO_WRITE_TOOL_NAME
    description: str = "Save investigation tasks to break down complex problems into manageable sub-tasks. ALWAYS provide the COMPLETE list of all tasks, not just the ones being updated."
    parameters: Dict[str, ToolParameter] = {
        "todos": ToolParameter(
            description="COMPLETE list of ALL tasks on the task list. Each task should have: id (string), content (string), status (pending/in_progress/completed/failed)",
            type="array",
            required=True,
            items=ToolParameter(
                type="object",
                properties={
                    "id": ToolParameter(type="string", required=True),
                    "content": ToolParameter(type="string", required=True),
                    "status": ToolParameter(
                        type="string",
                        required=True,
                        enum=["pending", "in_progress", "completed", "failed"],
                    ),
                },
            ),
        ),
    }

    # Print a nice table to console/log with in-place updating
    def print_tasks_table(self, tasks):
        """
        Display task table with in-place updating support.

        When running in an interactive terminal (TTY), this will update
        the display in place rather than printing multiple copies.
        """
        display_tasks(tasks)

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            todos_data = params.get("todos", [])

            tasks = parse_tasks(todos_data=todos_data)

            logging.debug(f"Tasks: {len(tasks)}")

            self.print_tasks_table(tasks)
            formatted_tasks = format_tasks(tasks)

            response_data = f"✅ Investigation plan updated with {len(tasks)} tasks. Tasks are now stored in session and will appear in subsequent prompts.\n\n"
            if formatted_tasks:
                response_data += formatted_tasks
            else:
                response_data += "No tasks currently in the investigation plan."

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response_data,
                params=params,
            )

        except Exception as e:
            logging.exception("error using todowrite tool")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to process tasks: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return "Update investigation tasks"


class CoreInvestigationToolset(Toolset):
    """Core toolset for investigation management and task planning."""

    def __init__(self):
        super().__init__(
            name="core_investigation",
            description="Core investigation tools for task management and planning",
            enabled=True,
            tools=[TodoWriteTool()],
            tags=[ToolsetTag.CORE],
            is_default=True,
        )

    def _reload_instructions(self):
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "investigator_instructions.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")
