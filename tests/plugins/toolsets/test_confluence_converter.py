import pytest

from holmes.plugins.toolsets.confluence.converter import confluence_storage_to_markdown


class TestHeadings:
    def test_h1(self):
        result = confluence_storage_to_markdown("<h1>Title</h1>")
        assert "# Title" in result

    def test_h2(self):
        result = confluence_storage_to_markdown("<h2>Subtitle</h2>")
        assert "## Subtitle" in result

    def test_h3(self):
        result = confluence_storage_to_markdown("<h3>Section</h3>")
        assert "### Section" in result


class TestInlineFormatting:
    def test_bold(self):
        result = confluence_storage_to_markdown("<p><strong>bold text</strong></p>")
        assert "**bold text**" in result

    def test_italic(self):
        result = confluence_storage_to_markdown("<p><em>italic text</em></p>")
        assert "*italic text*" in result

    def test_inline_code(self):
        result = confluence_storage_to_markdown("<p><code>some_function()</code></p>")
        assert "`some_function()`" in result

    def test_mixed_inline(self):
        result = confluence_storage_to_markdown(
            "<p>Normal <strong>bold</strong> and <code>code</code></p>"
        )
        assert "**bold**" in result
        assert "`code`" in result


class TestLinks:
    def test_external_link(self):
        result = confluence_storage_to_markdown(
            '<p><a href="https://example.com">Example</a></p>'
        )
        assert "[Example](https://example.com)" in result

    def test_internal_page_link(self):
        html = (
            '<ac:link><ri:page ri:content-title="My Page" ri:space-key="SRE" /></ac:link>'
        )
        result = confluence_storage_to_markdown(html)
        assert "[My Page]" in result

    def test_user_mention(self):
        html = '<ac:link><ri:user ri:account-id="abc123" /></ac:link>'
        result = confluence_storage_to_markdown(html)
        assert "@abc123" in result


class TestLists:
    def test_unordered_list(self):
        html = "<ul><li>First</li><li>Second</li><li>Third</li></ul>"
        result = confluence_storage_to_markdown(html)
        assert "- First" in result
        assert "- Second" in result
        assert "- Third" in result

    def test_ordered_list(self):
        html = "<ol><li>First</li><li>Second</li></ol>"
        result = confluence_storage_to_markdown(html)
        assert "1. First" in result
        assert "2. Second" in result


class TestCodeBlocks:
    def test_code_block_with_language(self):
        html = """
        <ac:structured-macro ac:name="code" ac:schema-version="1">
        <ac:parameter ac:name="language">python</ac:parameter>
        <ac:plain-text-body><![CDATA[def hello():
    print("world")]]></ac:plain-text-body>
        </ac:structured-macro>
        """
        result = confluence_storage_to_markdown(html)
        assert "```python" in result
        assert 'def hello():' in result
        assert '    print("world")' in result
        assert "```" in result

    def test_code_block_without_language(self):
        html = """
        <ac:structured-macro ac:name="code" ac:schema-version="1">
        <ac:plain-text-body><![CDATA[echo "hello"]]></ac:plain-text-body>
        </ac:structured-macro>
        """
        result = confluence_storage_to_markdown(html)
        assert "```" in result
        assert 'echo "hello"' in result

    def test_code_block_preserves_special_chars(self):
        html = """
        <ac:structured-macro ac:name="code" ac:schema-version="1">
        <ac:parameter ac:name="language">sql</ac:parameter>
        <ac:plain-text-body><![CDATA[SELECT * FROM users WHERE age > 18 AND name != 'admin';]]></ac:plain-text-body>
        </ac:structured-macro>
        """
        result = confluence_storage_to_markdown(html)
        assert "```sql" in result
        assert "SELECT * FROM users WHERE age > 18 AND name != 'admin';" in result

    def test_noformat_block(self):
        html = """
        <ac:structured-macro ac:name="noformat" ac:schema-version="1">
        <ac:plain-text-body><![CDATA[plain text output]]></ac:plain-text-body>
        </ac:structured-macro>
        """
        result = confluence_storage_to_markdown(html)
        assert "```" in result
        assert "plain text output" in result


class TestCalloutMacros:
    def test_warning_with_title(self):
        html = """
        <ac:structured-macro ac:name="warning" ac:schema-version="1">
        <ac:parameter ac:name="title">Danger</ac:parameter>
        <ac:rich-text-body><p>This is dangerous.</p></ac:rich-text-body>
        </ac:structured-macro>
        """
        result = confluence_storage_to_markdown(html)
        assert "> **WARNING: Danger**" in result
        assert "This is dangerous." in result

    def test_info_with_title(self):
        html = """
        <ac:structured-macro ac:name="info" ac:schema-version="1">
        <ac:parameter ac:name="title">Important</ac:parameter>
        <ac:rich-text-body><p>Some information.</p></ac:rich-text-body>
        </ac:structured-macro>
        """
        result = confluence_storage_to_markdown(html)
        assert "> **INFO: Important**" in result
        assert "Some information." in result

    def test_note(self):
        html = """
        <ac:structured-macro ac:name="note" ac:schema-version="1">
        <ac:rich-text-body><p>Remember this.</p></ac:rich-text-body>
        </ac:structured-macro>
        """
        result = confluence_storage_to_markdown(html)
        assert "> **NOTE:**" in result
        assert "Remember this." in result

    def test_tip(self):
        html = """
        <ac:structured-macro ac:name="tip" ac:schema-version="1">
        <ac:parameter ac:name="title">Pro Tip</ac:parameter>
        <ac:rich-text-body><p>Use shortcuts.</p></ac:rich-text-body>
        </ac:structured-macro>
        """
        result = confluence_storage_to_markdown(html)
        assert "> **TIP: Pro Tip**" in result
        assert "Use shortcuts." in result

    def test_multiline_callout_blockquoted(self):
        html = """
        <ac:structured-macro ac:name="warning" ac:schema-version="1">
        <ac:parameter ac:name="title">Alert</ac:parameter>
        <ac:rich-text-body>
        <p>Line one.</p>
        <p>Line two.</p>
        </ac:rich-text-body>
        </ac:structured-macro>
        """
        result = confluence_storage_to_markdown(html)
        # All content lines should be blockquoted
        lines = [l for l in result.split("\n") if l.strip() and "WARNING" in l or "Line" in l]
        for line in lines:
            assert line.startswith(">"), f"Expected blockquote, got: {line!r}"


class TestStatusMacro:
    def test_status_inline(self):
        html = '<ac:structured-macro ac:name="status" ac:schema-version="1"><ac:parameter ac:name="title">SEV-1</ac:parameter><ac:parameter ac:name="colour">Red</ac:parameter></ac:structured-macro>'
        result = confluence_storage_to_markdown(html)
        assert "[SEV-1]" in result

    def test_multiple_statuses(self):
        html = """
        <p>Priority: <ac:structured-macro ac:name="status" ac:schema-version="1"><ac:parameter ac:name="title">HIGH</ac:parameter></ac:structured-macro>
        Status: <ac:structured-macro ac:name="status" ac:schema-version="1"><ac:parameter ac:name="title">IN PROGRESS</ac:parameter></ac:structured-macro></p>
        """
        result = confluence_storage_to_markdown(html)
        assert "[HIGH]" in result
        assert "[IN PROGRESS]" in result


class TestTocMacro:
    def test_toc_removed(self):
        html = '<ac:structured-macro ac:name="toc" ac:schema-version="1"><ac:parameter ac:name="maxLevel">3</ac:parameter></ac:structured-macro>'
        result = confluence_storage_to_markdown(html)
        assert result.strip() == ""


class TestPanelMacro:
    def test_panel_with_title(self):
        html = """
        <ac:structured-macro ac:name="panel" ac:schema-version="1">
        <ac:parameter ac:name="title">Contacts</ac:parameter>
        <ac:rich-text-body><p>Call the SRE team.</p></ac:rich-text-body>
        </ac:structured-macro>
        """
        result = confluence_storage_to_markdown(html)
        assert "> **Contacts:**" in result
        assert "Call the SRE team." in result


class TestTasks:
    def test_incomplete_task(self):
        html = '<ac:task><ac:task-id>1</ac:task-id><ac:task-status>incomplete</ac:task-status><ac:task-body>Do this thing</ac:task-body></ac:task>'
        result = confluence_storage_to_markdown(html)
        assert "- [ ] Do this thing" in result

    def test_complete_task(self):
        html = '<ac:task><ac:task-id>2</ac:task-id><ac:task-status>complete</ac:task-status><ac:task-body>Already done</ac:task-body></ac:task>'
        result = confluence_storage_to_markdown(html)
        assert "- [x] Already done" in result

    def test_task_list(self):
        html = """
        <ac:task-list>
        <ac:task><ac:task-id>1</ac:task-id><ac:task-status>incomplete</ac:task-status><ac:task-body>First</ac:task-body></ac:task>
        <ac:task><ac:task-id>2</ac:task-id><ac:task-status>complete</ac:task-status><ac:task-body>Second</ac:task-body></ac:task>
        </ac:task-list>
        """
        result = confluence_storage_to_markdown(html)
        assert "- [ ] First" in result
        assert "- [x] Second" in result


class TestTables:
    def test_simple_table(self):
        html = """
        <table><tbody>
        <tr><th>Name</th><th>Value</th></tr>
        <tr><td>CPU</td><td>80%</td></tr>
        <tr><td>Memory</td><td>4GB</td></tr>
        </tbody></table>
        """
        result = confluence_storage_to_markdown(html)
        assert "| Name | Value |" in result
        assert "| --- | --- |" in result
        assert "| CPU | 80% |" in result
        assert "| Memory | 4GB |" in result

    def test_table_with_inline_formatting(self):
        html = """
        <table><tbody>
        <tr><th>Command</th><th>Risk</th></tr>
        <tr><td><code>kubectl delete pod</code></td><td><ac:structured-macro ac:name="status" ac:schema-version="1"><ac:parameter ac:name="title">HIGH</ac:parameter></ac:structured-macro></td></tr>
        </tbody></table>
        """
        result = confluence_storage_to_markdown(html)
        assert "`kubectl delete pod`" in result
        assert "[HIGH]" in result


class TestExpandMacro:
    def test_expand(self):
        html = """
        <ac:structured-macro ac:name="expand" ac:schema-version="1">
        <ac:parameter ac:name="title">Show details</ac:parameter>
        <ac:rich-text-body><p>Hidden content here.</p></ac:rich-text-body>
        </ac:structured-macro>
        """
        result = confluence_storage_to_markdown(html)
        assert "**Show details:**" in result
        assert "Hidden content here." in result


class TestFullPage:
    """Test a realistic full Confluence page (the runbook from our testing)."""

    RUNBOOK_HTML = """<ac:structured-macro ac:name="info" ac:schema-version="1">
<ac:parameter ac:name="title">Runbook Metadata</ac:parameter>
<ac:rich-text-body>
<p><strong>Service:</strong> checkout-api<br />
<strong>Severity:</strong> <ac:structured-macro ac:name="status" ac:schema-version="1"><ac:parameter ac:name="title">SEV-1</ac:parameter><ac:parameter ac:name="colour">Red</ac:parameter></ac:structured-macro></p>
</ac:rich-text-body>
</ac:structured-macro>

<ac:structured-macro ac:name="toc" ac:schema-version="1">
<ac:parameter ac:name="maxLevel">3</ac:parameter>
</ac:structured-macro>

<h1>Overview</h1>
<p>Database connection pool exhaustion runbook.</p>

<ac:structured-macro ac:name="warning" ac:schema-version="1">
<ac:parameter ac:name="title">Critical</ac:parameter>
<ac:rich-text-body><p>Causes <strong>cascading failures</strong>.</p></ac:rich-text-body>
</ac:structured-macro>

<h2>Step 1</h2>
<ac:structured-macro ac:name="code" ac:schema-version="1">
<ac:parameter ac:name="language">promql</ac:parameter>
<ac:plain-text-body><![CDATA[db_pool_active{service="checkout"}]]></ac:plain-text-body>
</ac:structured-macro>

<table><tbody>
<tr><th>Action</th><th>Risk</th></tr>
<tr><td>Kill queries</td><td><ac:structured-macro ac:name="status" ac:schema-version="1"><ac:parameter ac:name="title">HIGH</ac:parameter></ac:structured-macro></td></tr>
</tbody></table>

<ac:task-list>
<ac:task><ac:task-id>1</ac:task-id><ac:task-status>incomplete</ac:task-status><ac:task-body>File Jira ticket</ac:task-body></ac:task>
<ac:task><ac:task-id>2</ac:task-id><ac:task-status>complete</ac:task-status><ac:task-body>Add leak detection</ac:task-body></ac:task>
</ac:task-list>

<ul>
<li><ac:link><ri:page ri:content-title="Tuning Guide" ri:space-key="SRE" /></ac:link></li>
</ul>"""

    def test_preserves_all_semantic_elements(self):
        result = confluence_storage_to_markdown(self.RUNBOOK_HTML)

        # Info callout
        assert "INFO: Runbook Metadata" in result
        assert "**Service:** checkout-api" in result

        # Status lozenge
        assert "[SEV-1]" in result

        # TOC removed
        assert "maxLevel" not in result

        # Headings
        assert "# Overview" in result
        assert "## Step 1" in result

        # Warning callout
        assert "WARNING: Critical" in result
        assert "**cascading failures**" in result

        # Code block with language
        assert "```promql" in result
        assert 'db_pool_active{service="checkout"}' in result

        # Table
        assert "| Action | Risk |" in result
        assert "[HIGH]" in result

        # Tasks
        assert "- [ ] File Jira ticket" in result
        assert "- [x] Add leak detection" in result

        # Internal page link
        assert "[Tuning Guide]" in result

    def test_significant_token_savings(self):
        result = confluence_storage_to_markdown(self.RUNBOOK_HTML)
        savings = 1 - len(result) / len(self.RUNBOOK_HTML)
        # Should achieve at least 40% savings
        assert savings > 0.40, f"Only {savings*100:.0f}% savings"

    def test_no_xml_artifacts(self):
        result = confluence_storage_to_markdown(self.RUNBOOK_HTML)
        # No raw XML should leak through
        assert "ac:structured-macro" not in result
        assert "ac:parameter" not in result
        assert "ac:rich-text-body" not in result
        assert "ac:plain-text-body" not in result
        assert "ri:page" not in result
        assert "ri:space-key" not in result
        assert "schema-version" not in result
        assert "macro-id" not in result


class TestEdgeCases:
    def test_empty_input(self):
        result = confluence_storage_to_markdown("")
        assert result.strip() == ""

    def test_plain_text_only(self):
        result = confluence_storage_to_markdown("<p>Just plain text.</p>")
        assert "Just plain text." in result

    def test_invalid_xml_fallback(self):
        # Malformed HTML that can't parse as XML
        result = confluence_storage_to_markdown("<p>Unclosed tag<br>more text")
        # Should fallback to tag stripping
        assert "Unclosed tag" in result
        assert "more text" in result

    def test_unknown_macro_extracts_text(self):
        html = """
        <ac:structured-macro ac:name="some-custom-plugin" ac:schema-version="1">
        <ac:rich-text-body><p>Custom content</p></ac:rich-text-body>
        </ac:structured-macro>
        """
        result = confluence_storage_to_markdown(html)
        assert "Custom content" in result

    def test_nested_formatting(self):
        html = "<p><strong>Bold with <code>code inside</code></strong></p>"
        result = confluence_storage_to_markdown(html)
        assert "**Bold with `code inside`**" in result
