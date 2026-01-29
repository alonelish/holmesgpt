"""
Task display utility for in-place updating of task lists in the terminal.

Instead of printing multiple copies of the task list, this module provides
functionality to update the display in place using ANSI escape codes.
"""

import sys
import time
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from holmes.plugins.toolsets.investigator.model import Task

# Maximum time in seconds between prints to allow in-place updating.
# If more time has passed, we assume other output may have been printed
# and we should not try to clear it.
INPLACE_UPDATE_THRESHOLD_SECONDS = 2.0


class TaskDisplay:
    """
    Manages in-place display of task lists in the terminal.

    Uses ANSI escape codes to move the cursor up and clear lines,
    allowing the task list to be updated in place rather than
    printing multiple copies.
    """

    _instance: Optional["TaskDisplay"] = None
    _last_line_count: int = 0
    _last_print_time: float = 0.0

    def __new__(cls) -> "TaskDisplay":
        """Singleton pattern to ensure consistent state across calls."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._last_line_count = 0
            cls._instance._last_print_time = 0.0
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the display state. Useful for testing or starting fresh."""
        if cls._instance is not None:
            cls._instance._last_line_count = 0
            cls._instance._last_print_time = 0.0

    def _is_tty(self) -> bool:
        """Check if stdout is a terminal (TTY)."""
        try:
            return sys.stdout.isatty()
        except Exception:
            return False

    def _should_clear_previous(self) -> bool:
        """
        Determine if we should clear the previous output.

        We only clear if:
        1. We're in a TTY
        2. We have previous output to clear
        3. The last print was recent (within threshold)

        The time check helps avoid clearing unrelated output that may have
        been printed by other parts of the system (logging, tool outputs, etc.)
        """
        if not self._is_tty():
            return False
        if self._last_line_count <= 0:
            return False

        time_since_last = time.time() - self._last_print_time
        return time_since_last < INPLACE_UPDATE_THRESHOLD_SECONDS

    def _clear_previous_output(self) -> None:
        """Clear the previously printed task list using ANSI escape codes."""
        if self._last_line_count > 0:
            # Move cursor up and clear each line
            # \033[A = move cursor up one line
            # \033[2K = clear entire line
            for _ in range(self._last_line_count):
                sys.stdout.write("\033[A")  # Move up
                sys.stdout.write("\033[2K")  # Clear line
            sys.stdout.flush()

    def _count_lines(self, text: str) -> int:
        """Count the number of lines in the text, accounting for terminal width."""
        if not text:
            return 0
        return text.count("\n") + 1

    def display_tasks(self, tasks: List[Any]) -> None:
        """
        Display the task list, updating in place if in a TTY.

        Args:
            tasks: List of Task objects to display. Each task should have
                   id, content, and status attributes.
        """
        if not tasks:
            output = "No tasks in the investigation plan."
            self._display_output(output)
            return

        # Build the task table
        status_icons = {
            "pending": "[ ]",
            "in_progress": "[~]",
            "completed": "[✓]",
            "failed": "[✗]",
        }

        def get_status_value(task: Any) -> str:
            """Extract status value from task, handling both enum and string."""
            status = task.status
            if hasattr(status, "value"):
                return status.value
            return str(status)

        max_id_width = max(len(str(task.id)) for task in tasks)
        max_content_width = max(len(task.content) for task in tasks)
        max_status_display_width = max(
            len(
                f"{status_icons.get(get_status_value(task), '[?]')} {get_status_value(task)}"
            )
            for task in tasks
        )

        id_width = max(max_id_width, len("ID"))
        content_width = max(max_content_width, len("Content"))
        status_width = max(max_status_display_width, len("Status"))

        separator = f"+{'-' * (id_width + 2)}+{'-' * (content_width + 2)}+{'-' * (status_width + 2)}+"
        header = f"| {'ID':<{id_width}} | {'Content':<{content_width}} | {'Status':<{status_width}} |"

        lines = ["Task List:", separator, header, separator]

        for task in tasks:
            status_val = get_status_value(task)
            status_icon = status_icons.get(status_val, "[?]")
            status_display = f"{status_icon} {status_val}"
            row = f"| {task.id:<{id_width}} | {task.content:<{content_width}} | {status_display:<{status_width}} |"
            lines.append(row)

        lines.append(separator)
        output = "\n".join(lines)

        self._display_output(output)

    def _display_output(self, output: str) -> None:
        """
        Display output, clearing previous output if appropriate.

        Args:
            output: The text to display
        """
        if self._should_clear_previous():
            # Clear previous output and print new
            self._clear_previous_output()

        print(output)
        sys.stdout.flush()
        self._last_line_count = self._count_lines(output)
        self._last_print_time = time.time()


# Module-level convenience functions
_task_display = TaskDisplay()


def display_tasks(tasks: List[Any]) -> None:
    """
    Display the task list, updating in place if in a TTY.

    This is the main entry point for displaying task lists.
    It will automatically clear and redraw the previous output
    when running in an interactive terminal.

    Args:
        tasks: List of Task objects to display. Each task should have
               id, content, and status attributes.
    """
    _task_display.display_tasks(tasks)


def reset_task_display() -> None:
    """
    Reset the task display state.

    Call this when starting a new investigation or when the display
    state needs to be cleared (e.g., after printing other output
    that should not be cleared).
    """
    TaskDisplay.reset()
