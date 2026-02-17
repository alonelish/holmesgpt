import shutil
from io import StringIO
from typing import Any, Optional

from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.shortcuts import clear as pt_clear
from rich.console import Console as RichConsole


class HolmesConsole:
    """
    Centralized console output for Holmes CLI.

    Routes all user-facing output through prompt_toolkit for TUI compatibility.
    Uses Rich internally for rendering formatted content (Markdown, Tables, Panels, etc.)
    to ANSI strings, then outputs them via prompt_toolkit's print_formatted_text.

    This ensures a single output pathway that can be redirected to a TUI panel
    in the future without changing any calling code.
    """

    def __init__(self) -> None:
        self._buffer = ""

    def _get_width(self) -> int:
        """Get current terminal width for Rich rendering."""
        return shutil.get_terminal_size().columns

    def _render(self, *args: Any, **kwargs: Any) -> str:
        """Render content using Rich Console to an ANSI string."""
        buf = StringIO()
        console = RichConsole(
            file=buf,
            force_terminal=True,
            width=self._get_width(),
        )
        console.print(*args, **kwargs)
        return buf.getvalue()

    def print(
        self,
        *args: Any,
        markup: bool = True,
        end: str = "\n",
        style: Optional[str] = None,
        soft_wrap: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        Print formatted content through prompt_toolkit.

        Supports the same arguments as rich.console.Console.print():
        - Rich markup strings: "[bold red]Error[/bold red]"
        - Rich renderables: Markdown(), Panel(), Table(), Rule()
        - markup=False to disable Rich markup interpretation
        - end="" or end=" " for continuation printing
        - style parameter for applying a style to the entire output
        - soft_wrap for disabling word wrapping
        """
        rendered = self._render(
            *args,
            markup=markup,
            end="",
            style=style,
            soft_wrap=soft_wrap,
            **kwargs,
        )

        self._buffer += rendered

        if end == "\n":
            output = self._buffer
            self._buffer = ""
            if output:
                print_formatted_text(ANSI(output))
        elif end == "":
            pass
        else:
            self._buffer += end

    def clear(self) -> None:
        """Clear the terminal screen."""
        self._buffer = ""
        pt_clear()
