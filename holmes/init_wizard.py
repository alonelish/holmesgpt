"""Interactive setup wizard for HolmesGPT configuration."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml  # type: ignore
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

CONFIGURABLE_TOOLSETS: List[Dict[str, Any]] = [
    {
        "display_name": "Prometheus",
        "description": "Query metrics and alerting rules",
        "toolset_names": ["prometheus/metrics"],
        "fields": [
            {
                "key": "prometheus_url",
                "prompt": "Prometheus URL",
                "default": "http://localhost:9090",
                "secret": False,
            },
        ],
    },
    {
        "display_name": "Grafana",
        "description": "Dashboards, Loki logs, and Tempo traces",
        "toolset_names": ["grafana/dashboards", "grafana/loki", "grafana/tempo"],
        "fields": [
            {
                "key": "url",
                "prompt": "Grafana URL",
                "default": "http://localhost:3000",
                "secret": False,
            },
            {
                "key": "api_key",
                "prompt": "Grafana API key (optional, Enter to skip)",
                "default": "",
                "secret": True,
            },
        ],
    },
    {
        "display_name": "Datadog",
        "description": "Query logs, metrics, and traces",
        "toolset_names": ["datadog/general"],
        "fields": [
            {
                "key": "dd_api_key",
                "prompt": "Datadog API key",
                "default": "",
                "secret": True,
            },
            {
                "key": "dd_app_key",
                "prompt": "Datadog application key",
                "default": "",
                "secret": True,
            },
            {
                "key": "site_api_url",
                "prompt": "Datadog site API URL",
                "default": "https://api.datadoghq.com",
                "secret": False,
            },
        ],
    },
    {
        "display_name": "Elasticsearch / OpenSearch",
        "description": "Search and query log data",
        "toolset_names": ["elasticsearch/data"],
        "fields": [
            {
                "key": "url",
                "prompt": "Elasticsearch URL",
                "default": "",
                "secret": False,
            },
            {
                "key": "api_key",
                "prompt": "API key (optional, Enter to skip)",
                "default": "",
                "secret": True,
            },
        ],
    },
    {
        "display_name": "New Relic",
        "description": "Query application monitoring data via NRQL",
        "toolset_names": ["newrelic"],
        "fields": [
            {
                "key": "api_key",
                "prompt": "New Relic API key (NRAK-...)",
                "default": "",
                "secret": True,
            },
            {
                "key": "account_id",
                "prompt": "New Relic account ID",
                "default": "",
                "secret": False,
            },
        ],
    },
    {
        "display_name": "Coralogix",
        "description": "Query logs and observability data",
        "toolset_names": ["coralogix"],
        "fields": [
            {
                "key": "team_hostname",
                "prompt": "Team hostname (e.g., my-team)",
                "default": "",
                "secret": False,
            },
            {
                "key": "domain",
                "prompt": "Domain (e.g., eu2.coralogix.com)",
                "default": "",
                "secret": False,
            },
            {
                "key": "api_key",
                "prompt": "API key (cxuw_...)",
                "default": "",
                "secret": True,
            },
        ],
    },
]


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


def _prompt_toolsets(console: Console) -> Dict[str, Dict[str, Any]]:
    """Prompt user to select and configure data source toolsets."""
    console.print("\n[bold]Step 2: Data Sources[/bold]\n")
    console.print(
        "HolmesGPT auto-detects tools like kubectl, helm, and docker.\n"
        "You can also connect additional data sources:\n"
    )

    for i, ts in enumerate(CONFIGURABLE_TOOLSETS, 1):
        console.print(f"  {i}. {ts['display_name']} - {ts['description']}")

    console.print()
    console.print(
        "Enter numbers to configure (comma-separated), or press Enter to skip:"
    )
    selection = Prompt.ask("Data sources", default="")

    if not selection.strip():
        return {}

    selected_indices: List[int] = []
    for part in selection.split(","):
        part = part.strip()
        try:
            idx = int(part)
            if 1 <= idx <= len(CONFIGURABLE_TOOLSETS):
                selected_indices.append(idx - 1)
        except ValueError:
            pass

    if not selected_indices:
        return {}

    toolsets_config: Dict[str, Dict[str, Any]] = {}

    for idx in selected_indices:
        ts = CONFIGURABLE_TOOLSETS[idx]
        console.print(f"\n  [bold]{ts['display_name']}[/bold]")

        field_values: Dict[str, str] = {}
        for field in ts["fields"]:
            value = Prompt.ask(
                f"  {field['prompt']}",
                default=field.get("default", ""),
                password=field.get("secret", False),
            )
            if value:
                field_values[field["key"]] = value

        if field_values:
            toolset_names: List[str] = ts["toolset_names"]  # type: ignore[assignment]
            for toolset_name in toolset_names:
                toolsets_config[toolset_name] = {
                    "enabled": True,
                    "config": dict(field_values),
                }

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


def run_init_wizard(
    console: Console, config_path: Optional[Path] = None
) -> None:
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
