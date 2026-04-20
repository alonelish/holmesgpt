import json
import logging
import re
from typing import Any, ClassVar, Dict, Set, Tuple

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    ToolsetTag,
)
from holmes.plugins.toolsets.internet.internet import (
    InternetBaseToolset,
    scrape,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class FetchNotion(Tool):
    toolset: "InternetBaseToolset"

    def __init__(self, toolset: "InternetBaseToolset"):
        super().__init__(
            name="fetch_notion_webpage",
            description="Fetch a Notion webpage with HTTP requests and authentication.",
            parameters={
                "url": ToolParameter(
                    description="The URL to fetch",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,  # type: ignore
        )

    def convert_notion_url(self, url):
        if "api.notion.com" in url:
            return url
        # Strip query parameters before extracting ID
        url_without_params = url.split("?")[0].rstrip("/")
        match = re.search(r"-?(\w{32})$", url_without_params)
        if match:
            raw_id = match.group(1)
            # Format as UUID (Notion API requires dashed format)
            notion_id = f"{raw_id[:8]}-{raw_id[8:12]}-{raw_id[12:16]}-{raw_id[16:20]}-{raw_id[20:]}"
            return f"https://api.notion.com/v1/blocks/{notion_id}/children"
        return url  # Return original URL if no match is found

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        url: str = params["url"]

        # Get headers from the toolset configuration
        additional_headers = dict(
            self.toolset.internet_config.additional_headers if self.toolset.internet_config and self.toolset.internet_config.additional_headers else {}
        )
        # Notion API requires a version header
        additional_headers["Notion-Version"] = "2022-06-28"
        url = self.convert_notion_url(url)
        content, _ = scrape(url, additional_headers)

        if not content:
            logging.error(f"Failed to retrieve content from {url}")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to retrieve content from {url}",
                params=params,
            )

        # scrape() returns error message strings on HTTP failures - check if content is valid JSON
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logging.error(f"Notion API returned non-JSON response for {url}: {content[:200]}")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Notion API error: {content[:500]}",
                params=params,
            )

        logging.info(f"Notion API response for {url}: {json.dumps(parsed)[:500]}")

        result_data = self.parse_notion_content_from_dict(parsed)
        if not result_data:
            logging.warning(f"Notion page parsed to empty content. Raw response keys: {list(parsed.keys())}, results count: {len(parsed.get('results', []))}, block types: {[r.get('type') for r in parsed.get('results', [])]}")

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=result_data,
            params=params,
        )

    # Block types that contain rich_text directly under their type key
    RICH_TEXT_BLOCK_TYPES: ClassVar[Set[str]] = {
        "paragraph",
        "heading_1",
        "heading_2",
        "heading_3",
        "bulleted_list_item",
        "numbered_list_item",
        "quote",
        "callout",
        "toggle",
        "to_do",
    }

    HEADING_PREFIXES: ClassVar[Dict[str, str]] = {
        "heading_1": "# ",
        "heading_2": "## ",
        "heading_3": "### ",
    }

    def parse_notion_content_from_dict(self, data: dict) -> str:
        texts = []

        for result in data.get("results", []):
            block_type = result.get("type")
            if block_type not in self.RICH_TEXT_BLOCK_TYPES:
                continue

            block_data = result.get(block_type, {})
            rich_texts = block_data.get("rich_text", [])
            formatted_text = self.format_rich_text(rich_texts)
            if not formatted_text:
                continue

            # Apply block-type-specific formatting
            if block_type in self.HEADING_PREFIXES:
                formatted_text = f"{self.HEADING_PREFIXES[block_type]}{formatted_text}"
            elif block_type == "bulleted_list_item":
                formatted_text = f"- {formatted_text}"
            elif block_type == "numbered_list_item":
                formatted_text = f"1. {formatted_text}"
            elif block_type == "quote":
                formatted_text = f"> {formatted_text}"
            elif block_type == "to_do":
                checked = block_data.get("checked", False)
                checkbox = "[x]" if checked else "[ ]"
                formatted_text = f"- {checkbox} {formatted_text}"
            elif block_type == "toggle":
                formatted_text = f"▶ {formatted_text}"

            texts.append(formatted_text)

        return "\n\n".join(texts)

    def format_rich_text(self, rich_texts: list) -> str:
        """Helper function to apply formatting (bold, code, etc.)"""
        formatted_text = []
        for text in rich_texts:
            plain_text = text["text"]["content"]
            annotations = text.get("annotations", {})

            # Apply formatting
            if annotations.get("bold"):
                plain_text = f"**{plain_text}**"
            if annotations.get("code"):
                plain_text = f"`{plain_text}`"

            formatted_text.append(plain_text)

        return "".join(formatted_text)

    def get_parameterized_one_liner(self, params) -> str:
        url: str = params["url"]
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Fetch Webpage {url}"


class NotionToolset(InternetBaseToolset):
    def __init__(self):
        super().__init__(
            name="notion",
            description="Fetch notion webpages",
            icon_url="https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/notion.svg",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/notion/",
            tools=[
                FetchNotion(self),
            ],
            tags=[
                ToolsetTag.CORE,
            ],
        )

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        if not config or not config.get("additional_headers", {}):
            return (
                False,
                "Notion toolset is misconfigured. Authorization header is required.",
            )
        return super().prerequisites_callable(config)
