"""Interactive setup wizard for HolmesGPT configuration."""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml  # type: ignore
from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style as PTStyle
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from holmes.core.config import config_path_dir

AI_PROVIDERS: List[Dict[str, Any]] = [
    {
        "name": "OpenAI",
        "default_model": "gpt-4.1",
        "env_var": "OPENAI_API_KEY",
        "fast_model": "gpt-4o-mini",
    },
    {
        "name": "Anthropic",
        "default_model": "anthropic/claude-sonnet-4-5-20250929",
        "env_var": "ANTHROPIC_API_KEY",
        "fast_model": "anthropic/claude-haiku-3-5-20241022",
    },
    {
        "name": "Azure OpenAI",
        "default_model": "azure/<your-deployment-name>",
        "env_var": "AZURE_API_KEY",
        "fast_model": "",
    },
    {
        "name": "OpenRouter",
        "default_model": "openrouter/anthropic/claude-sonnet-4-5-20250929",
        "env_var": "OPENROUTER_API_KEY",
        "fast_model": "openrouter/anthropic/claude-haiku-3-5-20241022",
    },
    {
        "name": "AWS Bedrock",
        "default_model": "bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0",
        "env_var": None,
        "fast_model": "",
    },
    {
        "name": "Other (custom endpoint)",
        "default_model": "",
        "env_var": None,
        "fast_model": "",
    },
]

# Internal/utility toolsets hidden from the wizard
_INTERNAL_TOOLSETS = {
    "core_investigation",
    "bash",
    "connectivity_check",
    "robusta",
    "runbook",
    "kubectl_run",
    "opensearch_query_assist",
    "internet",
    "internet/notion",
}

# Field name patterns indicating connection/auth parameters worth prompting
_CONNECTION_FIELD_PATTERNS = {
    "url",
    "key",
    "token",
    "host",
    "domain",
    "account",
    "password",
    "username",
    "endpoint",
    "site",
    "api_base",
}

# Field name patterns indicating secrets (masked input)
_SECRET_FIELD_PATTERNS = {"key", "token", "password", "secret"}


@dataclass
class ToolsetEntry:
    """Metadata about a discoverable toolset for the wizard."""

    name: str
    description: str
    has_config: bool
    config_fields: List[Dict[str, Any]] = field(default_factory=list)
    env_vars: List[str] = field(default_factory=list)


def _is_secret_field(field_name: str, prop: Dict[str, Any]) -> bool:
    """Check if a config field should be treated as a secret."""
    if prop.get("format") == "password" or prop.get("writeOnly"):
        return True
    name_lower = field_name.lower()
    return any(p in name_lower for p in _SECRET_FIELD_PATTERNS)


def _is_user_facing_field(field_name: str, is_required: bool) -> bool:
    """Check if a config field should be shown to the user."""
    if is_required:
        return True
    name_lower = field_name.lower()
    return any(p in name_lower for p in _CONNECTION_FIELD_PATTERNS)


def _resolve_field_type(prop: Dict[str, Any]) -> str:
    """Extract the simple type from a JSON Schema property (handles Optional/anyOf)."""
    if "type" in prop:
        return str(prop["type"])
    if "anyOf" in prop:
        for option in prop["anyOf"]:
            if isinstance(option, dict) and option.get("type") != "null":
                return str(option.get("type", "string"))
    return "string"


def _extract_config_fields(toolset: Any) -> List[Dict[str, Any]]:
    """Extract promptable config fields from a toolset's config schema."""
    if not toolset.config_classes:
        return []

    config_cls = toolset.config_classes[0]
    try:
        schema = config_cls.model_json_schema()
    except Exception:
        return []

    properties = schema.get("properties", {})
    required_set = set(schema.get("required", []))

    fields: List[Dict[str, Any]] = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        if not _is_user_facing_field(name, name in required_set):
            continue

        field_type = _resolve_field_type(prop)
        if field_type in ("object", "array"):
            continue

        raw_default = prop.get("default")
        default = str(raw_default) if raw_default is not None else ""
        if default == "None":
            default = ""

        title = prop.get("title", name.replace("_", " ").title())

        fields.append(
            {
                "key": name,
                "prompt": title,
                "default": default,
                "secret": _is_secret_field(name, prop),
                "required": name in required_set,
            }
        )

    return fields


def _extract_env_vars(toolset: Any) -> List[str]:
    """Extract required environment variable names from toolset prerequisites."""
    env_vars: List[str] = []
    for prereq in toolset.prerequisites:
        if hasattr(prereq, "env") and isinstance(prereq.env, list):
            env_vars.extend(prereq.env)
    return env_vars


def _discover_toolsets() -> List[ToolsetEntry]:
    """Discover all available toolsets at runtime."""
    from holmes.plugins.toolsets import load_builtin_toolsets

    try:
        all_toolsets = load_builtin_toolsets(dal=None)
    except Exception:
        logging.warning("Failed to load builtin toolsets", exc_info=True)
        return []

    entries: List[ToolsetEntry] = []
    for ts in all_toolsets:
        if ts.name in _INTERNAL_TOOLSETS:
            continue

        config_fields = _extract_config_fields(ts)
        env_vars = _extract_env_vars(ts)

        entries.append(
            ToolsetEntry(
                name=ts.name,
                description=ts.description,
                has_config=bool(ts.config_classes),
                config_fields=config_fields,
                env_vars=env_vars,
            )
        )

    entries.sort(key=lambda e: e.name)
    return entries


def _run_searchable_multiselect(
    items: List[ToolsetEntry],
    console: Console,
) -> List[int]:
    """Run an interactive searchable multi-select list.

    Returns list of selected indices into `items`, or empty list if cancelled.
    """
    if not items:
        return []

    search_text = [""]
    cursor_pos = [0]
    selected: Set[int] = set()
    result: List[Optional[List[int]]] = [None]

    max_visible = 15

    def get_filtered() -> List[tuple[int, ToolsetEntry]]:
        query = search_text[0].lower()
        if not query:
            return list(enumerate(items))
        return [
            (i, item)
            for i, item in enumerate(items)
            if query in item.name.lower() or query in item.description.lower()
        ]

    def clamp_cursor(filtered: List[tuple[int, ToolsetEntry]]) -> None:
        if not filtered:
            cursor_pos[0] = 0
        elif cursor_pos[0] >= len(filtered):
            cursor_pos[0] = max(0, len(filtered) - 1)

    def get_scroll_offset(filtered_len: int) -> int:
        if filtered_len <= max_visible:
            return 0
        if cursor_pos[0] < max_visible:
            return 0
        return min(cursor_pos[0] - max_visible + 1, filtered_len - max_visible)

    def get_display() -> List[tuple[str, str]]:
        filtered = get_filtered()
        clamp_cursor(filtered)
        lines: List[tuple[str, str]] = []

        lines.append(("bold", "  Search: "))
        lines.append(("", search_text[0]))
        lines.append(("class:cursor", "\u258f"))
        lines.append(("", "\n\n"))

        if not filtered:
            lines.append(("class:dim", "  No matches found.\n"))
        else:
            scroll_off = get_scroll_offset(len(filtered))
            visible_end = min(scroll_off + max_visible, len(filtered))

            if scroll_off > 0:
                lines.append(("class:dim", f"    ... {scroll_off} more above\n"))

            for j in range(scroll_off, visible_end):
                orig_idx, item = filtered[j]
                check = "x" if orig_idx in selected else " "
                is_cursor = j == cursor_pos[0]

                if is_cursor:
                    lines.append(("bold", f"  > [{check}] {item.name}"))
                else:
                    lines.append(("", f"    [{check}] {item.name}"))
                lines.append(("class:dim", f"  {item.description}\n"))

            remaining = len(filtered) - visible_end
            if remaining > 0:
                lines.append(("class:dim", f"    ... {remaining} more below\n"))

        lines.append(("", "\n"))
        lines.append(("class:hint", f"  Selected: {len(selected)}"))
        lines.append(("class:dim", f" | Showing {len(filtered)} of {len(items)}\n"))
        lines.append(
            (
                "class:hint",
                "  Space: toggle | Enter: confirm | Esc: cancel\n",
            )
        )

        return lines

    bindings = KeyBindings()

    @bindings.add("up")
    def _up(event: Any) -> None:
        filtered = get_filtered()
        if filtered and cursor_pos[0] > 0:
            cursor_pos[0] -= 1

    @bindings.add("down")
    def _down(event: Any) -> None:
        filtered = get_filtered()
        if filtered and cursor_pos[0] < len(filtered) - 1:
            cursor_pos[0] += 1

    @bindings.add("space")
    def _toggle(event: Any) -> None:
        filtered = get_filtered()
        if filtered and 0 <= cursor_pos[0] < len(filtered):
            orig_idx = filtered[cursor_pos[0]][0]
            if orig_idx in selected:
                selected.discard(orig_idx)
            else:
                selected.add(orig_idx)

    @bindings.add("enter")
    def _confirm(event: Any) -> None:
        result[0] = sorted(selected)
        event.app.exit()

    @bindings.add("escape")
    @bindings.add("c-c")
    def _cancel(event: Any) -> None:
        result[0] = []
        event.app.exit()

    @bindings.add("c-h")
    @bindings.add("backspace")
    def _backspace(event: Any) -> None:
        if search_text[0]:
            search_text[0] = search_text[0][:-1]
            cursor_pos[0] = 0

    @bindings.add(Keys.Any)
    def _char(event: Any) -> None:
        char = event.data
        if char.isprintable() and len(char) == 1:
            search_text[0] += char
            cursor_pos[0] = 0

    style = PTStyle.from_dict(
        {
            "cursor": "#00ff00",
            "dim": "#888888",
            "hint": "#888888",
        }
    )

    layout = Layout(Window(FormattedTextControl(get_display, show_cursor=False)))

    app: Application[None] = Application(
        layout=layout,
        key_bindings=bindings,
        style=style,
        full_screen=False,
    )

    app.run()
    return result[0] if result[0] is not None else []


def _prompt_ai_provider(console: Console) -> Dict[str, Any]:
    """Prompt user to select and configure an AI provider."""
    console.print("\n[bold]Step 1: AI Provider[/bold]\n")
    console.print("Select your LLM provider:\n")

    for i, provider in enumerate(AI_PROVIDERS, 1):
        env_hint = ""
        if provider["env_var"]:
            has_key = bool(os.environ.get(str(provider["env_var"])))
            if has_key:
                env_hint = f" [green](${provider['env_var']} detected)[/green]"
            else:
                env_hint = f" [dim](${provider['env_var']})[/dim]"
        console.print(f"  {i}. {provider['name']}{env_hint}")

    console.print()
    choice_str = Prompt.ask(
        "Provider",
        choices=[str(i) for i in range(1, len(AI_PROVIDERS) + 1)],
        default="1",
    )
    provider = AI_PROVIDERS[int(choice_str) - 1]
    config: Dict[str, Any] = {}

    # Model
    default_model = str(provider["default_model"])
    if default_model:
        model = Prompt.ask("Model", default=default_model)
    else:
        model = Prompt.ask("Model (e.g., openai/gpt-4.1)")
    config["model"] = model

    # API key
    env_var = provider["env_var"]
    env_value = os.environ.get(str(env_var)) if env_var else None

    if env_value:
        console.print(f"  [green]\u2713[/green] Found ${env_var} in environment")
        use_env = Confirm.ask(
            "Use environment variable instead of storing key in config?",
            default=True,
        )
        if not use_env:
            api_key = Prompt.ask("API key", password=True)
            if api_key:
                config["api_key"] = api_key
    elif provider["name"] == "AWS Bedrock":
        console.print(
            "  [dim]AWS Bedrock uses AWS credentials"
            " (AWS_PROFILE or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY)[/dim]"
        )
    else:
        prompt_text = "API key"
        if env_var:
            prompt_text += f" (or set ${env_var} and press Enter to skip)"
        api_key = Prompt.ask(prompt_text, password=True, default="")
        if api_key:
            config["api_key"] = api_key
        elif env_var:
            console.print(
                f"  [dim]Remember to set ${env_var} before running Holmes.[/dim]"
            )

    # Azure-specific fields
    if provider["name"] == "Azure OpenAI":
        api_base = Prompt.ask(
            "Azure endpoint URL (e.g., https://your-resource.openai.azure.com)"
        )
        if api_base:
            config["api_base"] = api_base
        api_version = Prompt.ask("API version", default="2024-02-15-preview")
        if api_version:
            config["api_version"] = api_version

    # Custom endpoint
    if provider["name"] == "Other (custom endpoint)":
        api_base = Prompt.ask("API base URL", default="")
        if api_base:
            config["api_base"] = api_base

    # Fast model for summarization
    fast_model = str(provider["fast_model"])
    if fast_model:
        use_fast = Confirm.ask(
            f"Enable fast model for summarization? ({fast_model})",
            default=False,
        )
        if use_fast:
            config["fast_model"] = fast_model

    return config


def _prompt_toolset_config(
    entry: ToolsetEntry, console: Console
) -> Optional[Dict[str, str]]:
    """Prompt user for a toolset's configuration values.

    Returns dict of config values, or None if no values were provided.
    """
    if not entry.config_fields:
        return None

    console.print(f"\n  [bold]{entry.name}[/bold]")
    field_values: Dict[str, str] = {}
    for cfg_field in entry.config_fields:
        label = cfg_field["prompt"]
        if not cfg_field["required"]:
            label += " (optional, Enter to skip)"
        value = Prompt.ask(
            f"  {label}",
            default=cfg_field.get("default", ""),
            password=cfg_field.get("secret", False),
        )
        if value:
            field_values[cfg_field["key"]] = value

    return field_values if field_values else None


def _prompt_toolsets(console: Console) -> Dict[str, Dict[str, Any]]:
    """Prompt user to select and configure data source toolsets."""
    console.print("\n[bold]Step 2: Data Sources[/bold]\n")
    console.print(
        "HolmesGPT auto-detects tools like kubectl, helm, and docker.\n"
        "Select additional data sources to configure:\n"
    )

    entries = _discover_toolsets()
    if not entries:
        console.print("  [dim]No additional toolsets found. Continuing...[/dim]")
        return {}

    selected_indices = _run_searchable_multiselect(entries, console)

    if not selected_indices:
        return {}

    toolsets_config: Dict[str, Dict[str, Any]] = {}

    # Configure each selected toolset
    for idx in selected_indices:
        entry = entries[idx]

        if entry.config_fields:
            config_values = _prompt_toolset_config(entry, console)
            if config_values:
                toolsets_config[entry.name] = {
                    "enabled": True,
                    "config": config_values,
                }
            else:
                toolsets_config[entry.name] = {"enabled": True}
        else:
            toolsets_config[entry.name] = {"enabled": True}

        # Mention required env vars
        if entry.env_vars:
            env_list = ", ".join(f"${v}" for v in entry.env_vars)
            console.print(f"  [dim]{entry.name} requires env vars: {env_list}[/dim]")

    return toolsets_config


def _build_config_dict(
    ai_config: Dict[str, Any],
    toolsets_config: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the final configuration dictionary from wizard inputs."""
    config: Dict[str, Any] = {}

    for key in ("model", "api_key", "api_base", "api_version", "fast_model"):
        if key in ai_config:
            config[key] = ai_config[key]

    if toolsets_config:
        config["toolsets"] = toolsets_config

    return config


def run_init_wizard(console: Console, config_path: Optional[Path] = None) -> None:
    """Run the interactive setup wizard to create a HolmesGPT config file."""
    if config_path is None:
        config_path = Path(config_path_dir) / "config.yaml"

    console.print(
        Panel(
            "[bold]Welcome to HolmesGPT Setup[/bold]\n\n"
            "This wizard will create a configuration file to get you started.\n"
            f"You can edit the config later at: [dim]{config_path}[/dim]",
            border_style="blue",
        )
    )

    # Check for existing config
    if config_path.exists():
        overwrite = Confirm.ask(
            f"\n[yellow]Config already exists at {config_path}.[/yellow] Overwrite?",
            default=False,
        )
        if not overwrite:
            console.print("[dim]Setup cancelled.[/dim]")
            return

    # Step 1: AI provider
    ai_config = _prompt_ai_provider(console)

    # Step 2: Data sources
    toolsets_config = _prompt_toolsets(console)

    # Build config
    config_dict = _build_config_dict(ai_config, toolsets_config)
    config_yaml = yaml.dump(config_dict, default_flow_style=False, sort_keys=False)

    # Step 3: Review and write
    console.print("\n[bold]Step 3: Review[/bold]\n")

    # Mask API keys in preview
    preview_dict = dict(config_dict)
    if "api_key" in preview_dict:
        key = str(preview_dict["api_key"])
        if len(key) > 8:
            preview_dict["api_key"] = key[:4] + "..." + key[-4:]
    preview_yaml = yaml.dump(preview_dict, default_flow_style=False, sort_keys=False)
    console.print(Panel(preview_yaml, title=str(config_path), border_style="green"))

    if Confirm.ask("Write this configuration?", default=True):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(config_yaml)
        console.print(
            f"\n[green]\u2713[/green] Configuration written to [bold]{config_path}[/bold]"
        )
        console.print(
            "\nGet started: [bold]holmes ask 'why is my pod crashlooping?'[/bold]"
        )
    else:
        console.print("[dim]Setup cancelled. No files were written.[/dim]")
