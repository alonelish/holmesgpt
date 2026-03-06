import logging
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

from pydantic import Field

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)


class StaticToolDefinition(ToolsetConfig):
    """Definition of a single static tool."""

    name: str
    description: str
    response: str
    parameters: Optional[Dict[str, Dict[str, Any]]] = None


class StaticToolsetConfig(ToolsetConfig):
    """Configuration for the static data toolset."""

    tools: List[StaticToolDefinition] = Field(
        description="List of static tool definitions, each with a name, description, and canned response",
    )


class StaticTool(Tool):
    toolset: "StaticToolset" = None  # type: ignore
    response: str = ""

    def __init__(
        self,
        toolset: "StaticToolset",
        name: str,
        description: str,
        response: str,
        parameters: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        parsed_params = {}
        if parameters:
            for param_name, param_def in parameters.items():
                parsed_params[param_name] = ToolParameter(**param_def)

        super().__init__(
            name=name,
            description=description,
            parameters=parsed_params,
        )
        self.toolset = toolset
        self.response = response

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=self.response,
            params=params,
        )

    def get_parameterized_one_liner(self, params) -> str:
        return f"static: {self.name}"


class StaticToolset(Toolset):
    config_classes: ClassVar[List[Type[StaticToolsetConfig]]] = [StaticToolsetConfig]

    def __init__(self, name: str = "static", **kwargs):
        super().__init__(
            name=name,
            description=kwargs.get("description", "Static data toolset that returns predefined responses"),
            tools=[],
            tags=[ToolsetTag.CORE],
            is_default=False,
            enabled=kwargs.get("enabled", True),
            prerequisites=[CallablePrerequisite(callable=self._prerequisites_callable)],
        )
        self.config = kwargs.get("config")

    def _prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        try:
            parsed = StaticToolsetConfig(**config)
            self.tools = [
                StaticTool(
                    toolset=self,
                    name=tool_def.name,
                    description=tool_def.description,
                    response=tool_def.response,
                    parameters=tool_def.parameters,
                )
                for tool_def in parsed.tools
            ]
            return True, ""
        except Exception as e:
            return False, f"Failed to initialize static toolset: {e}"
