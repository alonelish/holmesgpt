"""Tests to verify AI safety prompt behavior: off by default, opt-in when enabled."""

import pytest

from holmes.plugins.prompts import load_and_render_prompt


SAFETY_MARKERS = [
    "# Safety & Guardrails",
    "## Content Harms",
    "## Jailbreaks – UPIA",
    "## Jailbreaks – XPIA",
    "## IP / Third-Party Content Regurgitation",
    "## Ungrounded Content",
]

TEMPLATES_WITH_FLAG = [
    "builtin://generic_ask.jinja2",
]

TEMPLATES_WITHOUT_FLAG = [
    "builtin://generic_ask_for_issue_conversation.jinja2",
    "builtin://generic_investigation.jinja2",
]


def _base_context(**overrides):
    ctx = {
        "toolsets": [],
        "cluster_name": "test-cluster",
        "issue": {"source_type": "test"},
        "investigation": "test investigation",
        "tools_called_for_investigation": [],
        "sections": {},
    }
    ctx.update(overrides)
    return ctx


class TestAISafetyOffByDefault:
    """AI safety prompt should NOT be included by default."""

    @pytest.mark.parametrize("template_path", TEMPLATES_WITH_FLAG)
    def test_flagged_templates_exclude_safety_by_default(self, template_path):
        rendered = load_and_render_prompt(template_path, _base_context())
        for marker in SAFETY_MARKERS:
            assert marker not in rendered, f"Safety content unexpectedly present in {template_path}"

    @pytest.mark.parametrize("template_path", TEMPLATES_WITHOUT_FLAG)
    def test_noflag_templates_exclude_safety(self, template_path):
        rendered = load_and_render_prompt(template_path, _base_context())
        for marker in SAFETY_MARKERS:
            assert marker not in rendered, f"Safety content unexpectedly present in {template_path}"


class TestAISafetyOptIn:
    """AI safety prompt should be included when explicitly enabled."""

    @pytest.mark.parametrize("template_path", TEMPLATES_WITH_FLAG)
    def test_flagged_templates_include_safety_when_enabled(self, template_path):
        rendered = load_and_render_prompt(
            template_path, _base_context(ai_safety_enabled=True)
        )
        for marker in SAFETY_MARKERS:
            assert marker in rendered, f"Safety content missing from {template_path} when enabled"


def test_ai_safety_template_exists():
    """Test that the AI safety template file exists and can be rendered."""
    rendered = load_and_render_prompt("builtin://_ai_safety.jinja2", {})
    for marker in SAFETY_MARKERS:
        assert marker in rendered
