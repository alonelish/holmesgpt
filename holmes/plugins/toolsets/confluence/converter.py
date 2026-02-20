"""Convert Confluence storage format (XHTML with ac:/ri: namespaces) to Markdown.

Confluence stores page bodies as XHTML with custom Atlassian namespaces for macros,
tasks, links, and other rich content. Generic HTML-to-markdown converters fail on
these because they don't understand ac:structured-macro, ac:plain-text-body (with
CDATA), ri:page links, etc.

This converter uses Python's stdlib xml.etree.ElementTree to parse the XML and walks
the tree to produce clean markdown — essentially a Python implementation of the XSLT
approach that produced the best results in testing (55% token savings with all semantic
information preserved: code blocks with language, warnings/info/notes, status lozenges,
task checkboxes, internal page links).
"""

import logging
import re
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

# Namespace URIs used in Confluence storage format
AC = "http://atlassian.com/ac"
RI = "http://atlassian.com/ri"

# Fully qualified tag names
AC_STRUCTURED_MACRO = f"{{{AC}}}structured-macro"
AC_PARAMETER = f"{{{AC}}}parameter"
AC_RICH_TEXT_BODY = f"{{{AC}}}rich-text-body"
AC_PLAIN_TEXT_BODY = f"{{{AC}}}plain-text-body"
AC_TASK_LIST = f"{{{AC}}}task-list"
AC_TASK = f"{{{AC}}}task"
AC_TASK_ID = f"{{{AC}}}task-id"
AC_TASK_STATUS = f"{{{AC}}}task-status"
AC_TASK_BODY = f"{{{AC}}}task-body"
AC_LINK = f"{{{AC}}}link"
RI_PAGE = f"{{{RI}}}page"
RI_USER = f"{{{RI}}}user"
RI_ATTACHMENT = f"{{{RI}}}attachment"


def _get_ac_attr(elem: ET.Element, attr: str) -> str:
    """Get an ac:-namespaced attribute value, trying both namespaced and bare forms."""
    return elem.get(f"{{{AC}}}{attr}", "") or elem.get(f"ac:{attr}", "") or elem.get(attr, "")


def _get_ri_attr(elem: ET.Element, attr: str) -> str:
    """Get an ri:-namespaced attribute value, trying both namespaced and bare forms."""
    return elem.get(f"{{{RI}}}{attr}", "") or elem.get(f"ri:{attr}", "") or elem.get(attr, "")


def _find_param(macro: ET.Element, param_name: str) -> str:
    """Find a named ac:parameter child and return its text."""
    for param in macro.iter(AC_PARAMETER):
        if _get_ac_attr(param, "name") == param_name:
            return (param.text or "").strip()
    return ""


def _get_rich_text_body(macro: ET.Element) -> str:
    """Extract and convert the ac:rich-text-body child."""
    for body in macro.iter(AC_RICH_TEXT_BODY):
        return _convert_children(body)
    return ""


def _get_plain_text_body(macro: ET.Element) -> str:
    """Extract the ac:plain-text-body child (often contains CDATA)."""
    for body in macro.iter(AC_PLAIN_TEXT_BODY):
        return body.text or ""
    return ""


def _convert_element(elem: ET.Element) -> str:
    """Convert a single XML element to markdown."""
    tag = elem.tag

    # Strip namespace prefix for standard HTML tags
    local_tag = tag.rsplit("}", 1)[-1] if "}" in tag else tag

    # --- Confluence macro handling ---
    if tag == AC_STRUCTURED_MACRO:
        return _convert_macro(elem)

    if tag == AC_TASK:
        return _convert_task(elem)

    if tag in (AC_TASK_ID, AC_TASK_STATUS):
        return ""  # Consumed by _convert_task

    if tag == AC_TASK_BODY:
        return _convert_children(elem)

    if tag == AC_TASK_LIST:
        return _convert_children(elem)

    if tag == AC_LINK:
        return _convert_ac_link(elem)

    if tag == RI_PAGE:
        title = _get_ri_attr(elem, "content-title")
        return f"[{title}]" if title else ""

    if tag == RI_USER:
        account_id = _get_ri_attr(elem, "account-id")
        return f"@{account_id}" if account_id else ""

    if tag == RI_ATTACHMENT:
        filename = _get_ri_attr(elem, "filename")
        return f"[attachment: {filename}]" if filename else ""

    if tag == AC_PARAMETER:
        return ""  # Parameters are consumed by their parent macro

    if tag in (AC_RICH_TEXT_BODY, AC_PLAIN_TEXT_BODY):
        return _convert_children(elem)

    # --- Standard HTML tags ---
    if local_tag == "h1":
        return f"\n# {_convert_children(elem).strip()}\n"

    if local_tag == "h2":
        return f"\n## {_convert_children(elem).strip()}\n"

    if local_tag == "h3":
        return f"\n### {_convert_children(elem).strip()}\n"

    if local_tag == "h4":
        return f"\n#### {_convert_children(elem).strip()}\n"

    if local_tag == "h5":
        return f"\n##### {_convert_children(elem).strip()}\n"

    if local_tag == "h6":
        return f"\n###### {_convert_children(elem).strip()}\n"

    if local_tag == "p":
        content = _convert_children(elem).strip()
        return f"\n{content}\n" if content else ""

    if local_tag in ("strong", "b"):
        return f"**{_convert_children(elem)}**"

    if local_tag in ("em", "i"):
        return f"*{_convert_children(elem)}*"

    if local_tag == "code":
        return f"`{_convert_children(elem)}`"

    if local_tag == "pre":
        return f"\n```\n{_convert_children(elem)}\n```\n"

    if local_tag == "a":
        href = elem.get("href", "")
        text = _convert_children(elem)
        if href:
            return f"[{text}]({href})"
        return text

    if local_tag in ("ul", "ol"):
        return "\n" + _convert_list(elem, local_tag) + "\n"

    if local_tag == "li":
        return _convert_children(elem).strip()

    if local_tag == "br":
        return "\n"

    if local_tag == "hr":
        return "\n---\n"

    if local_tag == "blockquote":
        content = _convert_children(elem).strip()
        lines = content.split("\n")
        return "\n" + "\n".join(f"> {line}" for line in lines) + "\n"

    if local_tag == "table":
        return "\n" + _convert_table(elem) + "\n"

    if local_tag in ("thead", "tbody", "tfoot"):
        return _convert_children(elem)

    if local_tag == "tr":
        return ""  # Handled by _convert_table

    if local_tag in ("th", "td"):
        return _convert_children(elem)

    if local_tag in ("div", "span", "section"):
        return _convert_children(elem)

    # Default: just process children
    return _convert_children(elem)


def _as_blockquote(text: str) -> str:
    """Prefix every line of text with '> ' for markdown blockquote."""
    lines = text.strip().split("\n")
    return "\n".join(f"> {line}" for line in lines)


def _convert_macro(macro: ET.Element) -> str:
    """Convert an ac:structured-macro to markdown."""
    macro_name = _get_ac_attr(macro, "name")

    if macro_name == "code":
        language = _find_param(macro, "language")
        code = _get_plain_text_body(macro)
        return f"\n```{language}\n{code}\n```\n"

    if macro_name == "warning":
        title = _find_param(macro, "title")
        body = _get_rich_text_body(macro).strip()
        header = f"**WARNING: {title}**" if title else "**WARNING**"
        return "\n" + _as_blockquote(f"{header}\n{body}") + "\n"

    if macro_name == "info":
        title = _find_param(macro, "title")
        body = _get_rich_text_body(macro).strip()
        header = f"**INFO: {title}**" if title else "**INFO**"
        return "\n" + _as_blockquote(f"{header}\n{body}") + "\n"

    if macro_name == "note":
        body = _get_rich_text_body(macro).strip()
        return "\n" + _as_blockquote(f"**NOTE:** {body}") + "\n"

    if macro_name == "tip":
        title = _find_param(macro, "title")
        body = _get_rich_text_body(macro).strip()
        header = f"**TIP: {title}**" if title else "**TIP**"
        return "\n" + _as_blockquote(f"{header}\n{body}") + "\n"

    if macro_name == "status":
        title = _find_param(macro, "title")
        return f"[{title}]" if title else ""

    if macro_name == "toc":
        return ""  # Table of contents — not useful for LLM

    if macro_name == "panel":
        title = _find_param(macro, "title")
        body = _get_rich_text_body(macro).strip()
        if title:
            return "\n" + _as_blockquote(f"**{title}:**\n{body}") + "\n"
        return "\n" + _as_blockquote(body) + "\n"

    if macro_name == "expand":
        title = _find_param(macro, "title") or "Details"
        body = _get_rich_text_body(macro).strip()
        return f"\n**{title}:**\n{body}\n"

    if macro_name == "excerpt":
        return _get_rich_text_body(macro)

    if macro_name == "anchor":
        return ""  # Anchors aren't useful for LLM

    if macro_name in ("noformat", "preformatted"):
        code = _get_plain_text_body(macro)
        return f"\n```\n{code}\n```\n"

    # Unknown macro — extract any text content
    body = _get_rich_text_body(macro) or _get_plain_text_body(macro)
    if body.strip():
        return body
    return ""


def _convert_task(task: ET.Element) -> str:
    """Convert an ac:task to a markdown checkbox item."""
    status = ""
    body = ""
    for child in task:
        if child.tag == AC_TASK_STATUS:
            status = (child.text or "").strip()
        elif child.tag == AC_TASK_BODY:
            body = _convert_children(child).strip()

    checkbox = "[x]" if status == "complete" else "[ ]"
    return f"\n- {checkbox} {body}"


def _convert_ac_link(link: ET.Element) -> str:
    """Convert an ac:link to markdown."""
    for child in link:
        if child.tag == RI_PAGE:
            title = _get_ri_attr(child, "content-title")
            return f"[{title}]" if title else ""
        if child.tag == RI_USER:
            account_id = _get_ri_attr(child, "account-id")
            return f"@{account_id}" if account_id else ""
        if child.tag == RI_ATTACHMENT:
            filename = _get_ri_attr(child, "filename")
            return f"[attachment: {filename}]" if filename else ""
    # Fallback: extract text
    return _convert_children(link)


def _convert_list(elem: ET.Element, list_type: str) -> str:
    """Convert ul/ol to markdown list items."""
    lines = []
    for idx, child in enumerate(elem):
        local_tag = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
        if local_tag == "li":
            content = _convert_children(child).strip()
            if list_type == "ol":
                lines.append(f"{idx + 1}. {content}")
            else:
                lines.append(f"- {content}")
    return "\n".join(lines)


def _convert_table(table: ET.Element) -> str:
    """Convert an HTML table to markdown table format."""
    rows: list[list[str]] = []

    for elem in table.iter():
        local_tag = elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag
        if local_tag == "tr":
            cells = []
            for cell in elem:
                cell_tag = cell.tag.rsplit("}", 1)[-1] if "}" in cell.tag else cell.tag
                if cell_tag in ("th", "td"):
                    cells.append(_convert_children(cell).strip())
            if cells:
                rows.append(cells)

    if not rows:
        return ""

    # Determine column count from widest row
    max_cols = max(len(row) for row in rows)

    # Pad rows to same width
    for row in rows:
        while len(row) < max_cols:
            row.append("")

    lines = []
    for i, row in enumerate(rows):
        lines.append("| " + " | ".join(row) + " |")
        if i == 0:
            lines.append("| " + " | ".join("---" for _ in row) + " |")

    return "\n".join(lines)


def _convert_children(elem: ET.Element) -> str:
    """Convert all children of an element, including text and tail."""
    parts = []

    if elem.text:
        parts.append(elem.text)

    for child in elem:
        parts.append(_convert_element(child))
        if child.tail:
            parts.append(child.tail)

    return "".join(parts)


def _normalize_whitespace(text: str) -> str:
    """Clean up excessive blank lines while preserving code blocks."""
    # Collapse 3+ newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove leading/trailing whitespace on each line (but not inside code blocks)
    lines = text.split("\n")
    in_code_block = False
    cleaned = []
    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            cleaned.append(line)
        elif in_code_block:
            cleaned.append(line)  # Preserve code block content exactly
        else:
            cleaned.append(line.rstrip())

    return "\n".join(cleaned).strip() + "\n"


def confluence_storage_to_markdown(html: str) -> str:
    """Convert Confluence storage format HTML to clean Markdown.

    Args:
        html: Confluence storage format body (XHTML with ac:/ri: namespaces).

    Returns:
        Clean markdown text with code blocks, warnings, status lozenges,
        task checkboxes, and internal links preserved.
    """
    # Wrap in root element with namespace declarations so the XML parser
    # can resolve ac: and ri: prefixed tags and attributes.
    wrapped = (
        f'<root xmlns:ac="{AC}" xmlns:ri="{RI}">'
        f"{html}"
        f"</root>"
    )

    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError as e:
        logger.warning("Failed to parse Confluence storage format as XML: %s", e)
        # Fallback: strip tags and return plain text
        return re.sub(r"<[^>]+>", "", html)

    result = _convert_children(root)
    return _normalize_whitespace(result)
