# ruff: noqa: E402
import os

from holmes.utils.cert_utils import add_custom_certificate

ADDITIONAL_CERTIFICATE: str = os.environ.get("CERTIFICATE", "")
if add_custom_certificate(ADDITIONAL_CERTIFICATE):
    print("added custom certificate")

# DO NOT ADD ANY IMPORTS OR CODE ABOVE THIS LINE
# IMPORTING ABOVE MIGHT INITIALIZE AN HTTPS CLIENT THAT DOESN'T TRUST THE CUSTOM CERTIFICATE
import sys
from holmes.utils.colors import USER_COLOR
import json
import logging
import socket
import uuid
from pathlib import Path
from typing import List, Optional

import typer
from holmes import get_version  # type: ignore
from holmes.config import (
    DEFAULT_CONFIG_LOCATION,
    Config,
    SourceFactory,
    SupportedTicketSources,
)
from holmes.core.prompt import build_initial_ask_messages
from holmes.core.resource_instruction import ResourceInstructionDocument
from holmes.core.tools import pretty_print_toolset_status
from holmes.core.tracing import SpanType, TracingFactory
from holmes.interactive import run_interactive_loop
from holmes.plugins.destinations import DestinationType
from holmes.plugins.interfaces import Issue
from holmes.plugins.sources.opsgenie import OPSGENIE_TEAM_INTEGRATION_KEY_HELP
from holmes.utils.console.logging import init_logging
from holmes.utils.console.result import handle_result
from holmes.utils.file_utils import write_json_file

app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)


investigate_app = typer.Typer(
    add_completion=False,
    name="investigate",
    no_args_is_help=True,
    help="Investigate firing alerts or tickets",
)
app.add_typer(investigate_app, name="investigate")
generate_app = typer.Typer(
    add_completion=False,
    name="generate",
    no_args_is_help=True,
    help="Generate new integrations or test data",
)
app.add_typer(generate_app, name="generate")
toolset_app = typer.Typer(
    add_completion=False,
    name="toolset",
    no_args_is_help=True,
    help="Toolset management commands",
)
app.add_typer(toolset_app, name="toolset")


# Common cli options
# The defaults for options that are also in the config file MUST be None or else the cli defaults will override settings in the config file
opt_api_key: Optional[str] = typer.Option(
    None,
    help="API key to use for the LLM (if not given, uses environment variables OPENAI_API_KEY or AZURE_API_KEY)",
)
opt_model: Optional[str] = typer.Option(None, help="Model to use for the LLM")
opt_fast_model: Optional[str] = typer.Option(
    None, help="Optional fast model for summarization tasks"
)
opt_config_file: Optional[Path] = typer.Option(
    DEFAULT_CONFIG_LOCATION,  # type: ignore
    "--config",
    help="Path to the config file. Defaults to ~/.holmes/config.yaml when it exists. Command line arguments take precedence over config file settings",
)
opt_custom_toolsets: Optional[List[Path]] = typer.Option(
    [],
    "--custom-toolsets",
    "-t",
    help="Path to a custom toolsets. The status of the custom toolsets specified here won't be cached (can specify -t multiple times to add multiple toolsets)",
)
opt_max_steps: Optional[int] = typer.Option(
    40,
    "--max-steps",
    help="Advanced. Maximum number of steps the LLM can take to investigate the issue",
)
opt_verbose: Optional[List[bool]] = typer.Option(
    [],
    "--verbose",
    "-v",
    help="Verbose output. You can pass multiple times to increase the verbosity. e.g. -v or -vv or -vvv",
)
opt_log_costs: bool = typer.Option(
    False,
    "--log-costs",
    help="Show LLM cost information in the output",
)
opt_echo_request: bool = typer.Option(
    True,
    "--echo/--no-echo",
    help="Echo back the question provided to HolmesGPT in the output",
)
opt_destination: Optional[DestinationType] = typer.Option(
    DestinationType.CLI,
    "--destination",
    help="Destination for the results of the investigation (defaults to STDOUT)",
)
opt_slack_token: Optional[str] = typer.Option(
    None,
    "--slack-token",
    help="Slack API key if --destination=slack (experimental). Can generate with `pip install robusta-cli && robusta integrations slack`",
)
opt_slack_channel: Optional[str] = typer.Option(
    None,
    "--slack-channel",
    help="Slack channel if --destination=slack (experimental). E.g. #devops",
)
opt_json_output_file: Optional[str] = typer.Option(
    None,
    "--json-output-file",
    help="Save the complete output in json format in to a file",
    envvar="HOLMES_JSON_OUTPUT_FILE",
)

opt_documents: Optional[str] = typer.Option(
    None,
    "--documents",
    help="Additional documents to provide the LLM (typically URLs to runbooks)",
)


def parse_documents(documents: Optional[str]) -> List[ResourceInstructionDocument]:
    resource_documents = []

    if documents is not None:
        data = json.loads(documents)
        for item in data:
            resource_document = ResourceInstructionDocument(**item)
            resource_documents.append(resource_document)

    return resource_documents


# TODO: add streaming output
@app.command()
def ask(
    prompt: Optional[str] = typer.Argument(
        None, help="What to ask the LLM (user prompt)"
    ),
    prompt_file: Optional[Path] = typer.Option(
        None,
        "--prompt-file",
        "-pf",
        help="File containing the prompt to ask the LLM (overrides the prompt argument if provided)",
    ),
    # common options
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    fast_model: Optional[str] = opt_fast_model,
    config_file: Optional[Path] = opt_config_file,
    custom_toolsets: Optional[List[Path]] = opt_custom_toolsets,
    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose,
    log_costs: bool = opt_log_costs,
    # semi-common options
    destination: Optional[DestinationType] = opt_destination,
    slack_token: Optional[str] = opt_slack_token,
    slack_channel: Optional[str] = opt_slack_channel,
    show_tool_output: bool = typer.Option(
        False,
        "--show-tool-output",
        help="Advanced. Show the output of each tool that was called",
    ),
    include_file: Optional[List[Path]] = typer.Option(
        [],
        "--file",
        "-f",
        help="File to append to prompt (can specify -f multiple times to add multiple files)",
    ),
    json_output_file: Optional[str] = opt_json_output_file,
    echo_request: bool = opt_echo_request,
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        "-i/-n",
        help="Enter interactive mode after the initial question? For scripting, disable this with --no-interactive",
    ),
    refresh_toolsets: bool = typer.Option(
        False,
        "--refresh-toolsets",
        help="Refresh the toolsets status",
    ),
    trace: Optional[str] = typer.Option(
        None,
        "--trace",
        help="Enable tracing to the specified provider (e.g., 'braintrust')",
    ),
    system_prompt_additions: Optional[str] = typer.Option(
        None,
        "--system-prompt-additions",
        help="Additional content to append to the system prompt",
    ),
    bash_always_deny: bool = typer.Option(
        False,
        "--bash-always-deny",
        help="Auto-deny all bash commands not in allow list without prompting",
    ),
    bash_always_allow: bool = typer.Option(
        False,
        "--bash-always-allow",
        help="Bypass bash command approval checks. Recommended only for sandboxed environments",
    ),
):
    """
    Ask any question and answer using available tools
    """
    # Validate mutually exclusive flags
    if bash_always_deny and bash_always_allow:
        raise typer.BadParameter(
            "--bash-always-deny and --bash-always-allow are mutually exclusive. Choose one."
        )

    console = init_logging(verbose, log_costs)  # type: ignore
    # Detect and read piped input
    piped_data = None

    # when attaching a pycharm debugger sys.stdin.isatty() returns false and sys.stdin.read() is stuck
    running_from_pycharm = os.environ.get("PYCHARM_HOSTED", False)

    if not sys.stdin.isatty() and not running_from_pycharm:
        piped_data = sys.stdin.read().strip()
        if interactive:
            console.print(
                "[bold yellow]Interactive mode disabled when reading piped input[/bold yellow]"
            )
            interactive = False

    config = Config.load_from_file(
        config_file,
        api_key=api_key,
        model=model,
        fast_model=fast_model,
        max_steps=max_steps,
        custom_toolsets_from_cli=custom_toolsets,
        slack_token=slack_token,
        slack_channel=slack_channel,
    )

    # Create tracer if trace option is provided
    tracer = TracingFactory.create_tracer(trace, project="HolmesGPT-CLI")
    tracer.start_experiment()

    ai = config.create_console_toolcalling_llm(
        dal=None,  # type: ignore
        refresh_toolsets=refresh_toolsets,  # flag to refresh the toolset status
        tracer=tracer,
        model_name=model,
    )

    if prompt_file and prompt:
        raise typer.BadParameter(
            "You cannot provide both a prompt argument and a prompt file. Please use one or the other."
        )
    elif prompt_file:
        if not prompt_file.is_file():
            raise typer.BadParameter(f"Prompt file not found: {prompt_file}")
        with prompt_file.open("r") as f:
            prompt = f.read()
        console.print(
            f"[bold yellow]Loaded prompt from file {prompt_file}[/bold yellow]"
        )
    elif not prompt and not interactive and not piped_data:
        raise typer.BadParameter(
            "Either the 'prompt' argument or the --prompt-file option must be provided (unless using --interactive mode)."
        )

    # Handle piped data
    if piped_data:
        if prompt:
            # User provided both piped data and a prompt
            prompt = f"Here's some piped output:\n\n{piped_data}\n\n{prompt}"
        else:
            # Only piped data, no prompt - ask what to do with it
            prompt = f"Here's some piped output:\n\n{piped_data}\n\nWhat can you tell me about this output?"

    if echo_request and not interactive and prompt:
        console.print(f"[bold {USER_COLOR}]User:[/bold {USER_COLOR}] {prompt}")

    if interactive:
        run_interactive_loop(
            ai,
            console,
            prompt,
            include_file,
            show_tool_output,
            tracer,
            config.get_runbook_catalog(),
            system_prompt_additions,
            json_output_file=json_output_file,
            bash_always_deny=bash_always_deny,
            bash_always_allow=bash_always_allow,
        )
        return

    if include_file:
        for file_path in include_file:
            console.print(
                f"[bold yellow]Adding file {file_path} to context[/bold yellow]"
            )

    messages = build_initial_ask_messages(
        prompt,  # type: ignore
        include_file,
        ai.tool_executor,
        config.get_runbook_catalog(),
        system_prompt_additions,
    )

    with tracer.start_trace(
        f'holmes ask "{prompt}"', span_type=SpanType.TASK
    ) as trace_span:
        trace_span.log(input=prompt, metadata={"type": "user_question"})
        response = ai.call(messages, trace_span=trace_span)
        trace_span.log(
            output=response.result,
        )
        trace_url = tracer.get_trace_url()

    messages = response.messages  # type: ignore # Update messages with the full history

    if json_output_file:
        write_json_file(json_output_file, response.model_dump())

    issue = Issue(
        id=str(uuid.uuid4()),
        name=prompt,  # type: ignore
        source_type="holmes-ask",
        raw={"prompt": prompt, "full_conversation": messages},
        source_instance_id=socket.gethostname(),
    )
    handle_result(
        response,
        console,
        destination,  # type: ignore
        config,
        issue,
        show_tool_output,
        False,  # type: ignore
        log_costs,
    )

    if trace_url:
        console.print(f"🔍 View trace: {trace_url}")


def _investigate_issues(
    ai,
    issues: list,
    config: Config,
    console,
    source=None,
    update: bool = False,
    json_output_file: Optional[str] = None,
    source_name: str = "issue",
):
    """Shared logic for all investigate subcommands: loop over issues, ask Holmes about each one."""
    results = []
    for i, issue in enumerate(issues):
        console.print(
            f"[bold yellow]Analyzing {source_name} {i+1}/{len(issues)}: {issue.name}...[/bold yellow]"
        )

        prompt = f"Investigate this alert / issue and provide root cause analysis.\n\nTitle: {issue.name}"
        if getattr(issue, "description", None):
            prompt += f"\nDescription: {issue.description}"
        prompt += f"\n\nContext:\n{json.dumps(issue.raw, indent=2)}"

        messages = build_initial_ask_messages(
            prompt, [], ai.tool_executor, config.get_runbook_catalog(), None
        )
        result = ai.call(messages)

        handle_result(result, console, DestinationType.CLI, config, issue, False, True)

        if update and source:
            source.write_back_result(issue.id, result)
            console.print(f"[bold]Updated {issue.url}.[/bold]")

        results.append({"issue": issue.model_dump(), "result": result.model_dump()})

    if json_output_file:
        write_json_file(json_output_file, results)


@investigate_app.command()
def alertmanager(
    alertmanager_url: Optional[str] = typer.Option(None, help="AlertManager url"),
    alertmanager_alertname: Optional[str] = typer.Option(
        None,
        help="Investigate all alerts with this name (can be regex that matches multiple alerts). If not given, defaults to all firing alerts",
    ),
    alertmanager_label: Optional[List[str]] = typer.Option(
        [],
        help="For filtering alerts with a specific label. Must be of format key=value. If --alertmanager-label is passed multiple times, alerts must match ALL labels",
    ),
    alertmanager_username: Optional[str] = typer.Option(
        None, help="Username to use for basic auth"
    ),
    alertmanager_password: Optional[str] = typer.Option(
        None, help="Password to use for basic auth"
    ),
    alertmanager_file: Optional[Path] = typer.Option(
        None, help="Load alertmanager alerts from a file (used by the test framework)"
    ),
    alertmanager_limit: Optional[int] = typer.Option(
        None, "-n", help="Limit the number of alerts to process"
    ),
    # common options
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    config_file: Optional[Path] = opt_config_file,
    custom_toolsets: Optional[List[Path]] = opt_custom_toolsets,
    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose,
    # advanced options
    destination: Optional[DestinationType] = opt_destination,
    slack_token: Optional[str] = opt_slack_token,
    slack_channel: Optional[str] = opt_slack_channel,
    json_output_file: Optional[str] = opt_json_output_file,
):
    """
    Investigate Prometheus/Alertmanager alerts
    """
    console = init_logging(verbose)
    config = Config.load_from_file(
        config_file,
        api_key=api_key,
        model=model,
        max_steps=max_steps,
        alertmanager_url=alertmanager_url,
        alertmanager_username=alertmanager_username,
        alertmanager_password=alertmanager_password,
        alertmanager_alertname=alertmanager_alertname,
        alertmanager_label=alertmanager_label,
        alertmanager_file=alertmanager_file,
        slack_token=slack_token,
        slack_channel=slack_channel,
        custom_toolsets_from_cli=custom_toolsets,
    )
    ai = config.create_console_toolcalling_llm(dal=None, model_name=model)

    source = config.create_alertmanager_source()
    try:
        issues = source.fetch_issues()
    except Exception as e:
        logging.error("Failed to fetch issues from alertmanager", exc_info=e)
        return

    if alertmanager_limit is not None:
        console.print(
            f"[bold yellow]Limiting to {alertmanager_limit}/{len(issues)} issues.[/bold yellow]"
        )
        issues = issues[:alertmanager_limit]

    console.print(
        f"[bold yellow]Analyzing {len(issues)} alerts.[/bold yellow] [red]Press Ctrl+C to stop.[/red]"
    )
    _investigate_issues(
        ai,
        issues,
        config,
        console,
        json_output_file=json_output_file,
        source_name="alert",
    )


@investigate_app.command()
def jira(
    jira_url: Optional[str] = typer.Option(
        None,
        help="Jira url - e.g. https://your-company.atlassian.net",
        envvar="JIRA_URL",
    ),
    jira_username: Optional[str] = typer.Option(
        None,
        help="The email address with which you log into Jira",
        envvar="JIRA_USERNAME",
    ),
    jira_api_key: str = typer.Option(None, envvar="JIRA_API_KEY"),
    jira_query: Optional[str] = typer.Option(
        None,
        help="Investigate tickets matching a JQL query (e.g. 'project=DEFAULT_PROJECT')",
    ),
    update: Optional[bool] = typer.Option(False, help="Update Jira with AI results"),
    # common options
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    config_file: Optional[Path] = opt_config_file,
    custom_toolsets: Optional[List[Path]] = opt_custom_toolsets,
    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose,
    json_output_file: Optional[str] = opt_json_output_file,
):
    """
    Investigate Jira tickets
    """
    console = init_logging(verbose)
    config = Config.load_from_file(
        config_file,
        api_key=api_key,
        model=model,
        max_steps=max_steps,
        jira_url=jira_url,
        jira_username=jira_username,
        jira_api_key=jira_api_key,
        jira_query=jira_query,
        custom_toolsets_from_cli=custom_toolsets,
    )
    ai = config.create_console_toolcalling_llm(dal=None, model_name=model)
    source = config.create_jira_source()
    try:
        issues = source.fetch_issues()
    except Exception as e:
        logging.error("Failed to fetch issues from Jira", exc_info=e)
        return

    console.print(
        f"[bold yellow]Analyzing {len(issues)} Jira tickets.[/bold yellow] [red]Press Ctrl+C to stop.[/red]"
    )
    _investigate_issues(
        ai,
        issues,
        config,
        console,
        source=source,
        update=update,
        json_output_file=json_output_file,
        source_name="Jira ticket",
    )


@investigate_app.command()
def ticket(
    prompt: str = typer.Argument(help="What to ask the LLM (user prompt)"),
    source: SupportedTicketSources = typer.Option(
        ...,
        help=f"Source system to investigate the ticket from. Supported sources: {', '.join(s.value for s in SupportedTicketSources)}",
    ),
    ticket_url: Optional[str] = typer.Option(
        None, help="URL - e.g. https://your-company.atlassian.net", envvar="TICKET_URL"
    ),
    ticket_username: Optional[str] = typer.Option(
        None,
        help="The email address with which you log into your Source",
        envvar="TICKET_USERNAME",
    ),
    ticket_api_key: Optional[str] = typer.Option(None, envvar="TICKET_API_KEY"),
    ticket_id: Optional[str] = typer.Option(
        None, help="ticket ID to investigate (e.g., 'KAN-1')"
    ),
    config_file: Optional[Path] = opt_config_file,
    model: Optional[str] = opt_model,
):
    """
    Fetch and investigate a ticket from the specified source
    """
    console = init_logging([])
    try:
        ticket_source = SourceFactory.create_source(
            source=source,
            config_file=config_file,
            ticket_url=ticket_url,
            ticket_username=ticket_username,
            ticket_api_key=ticket_api_key,
            ticket_id=ticket_id,
        )
    except Exception as e:
        console.print(f"[bold red]Error: {str(e)}[/bold red]")
        return

    try:
        issue_to_investigate = ticket_source.source.fetch_issue(id=ticket_id)
        if issue_to_investigate is None:
            raise Exception(f"Issue {ticket_id} Not found")
    except Exception as e:
        logging.error(f"Failed to fetch issue from {source}", exc_info=e)
        console.print(
            f"[bold red]Error: Failed to fetch issue {ticket_id} from {source}.[/bold red]"
        )
        return

    ai = ticket_source.config.create_console_toolcalling_llm(dal=None, model_name=model)

    full_prompt = (
        f"{prompt} for issue '{issue_to_investigate.name}'"
        f" with description: '{issue_to_investigate.description}'"
    )
    messages = build_initial_ask_messages(
        full_prompt,
        [],
        ai.tool_executor,
        ticket_source.config.get_runbook_catalog(),
        None,
    )
    result = ai.call(messages)

    handle_result(
        result,
        console,
        DestinationType.CLI,
        ticket_source.config,
        issue_to_investigate,
        False,
        True,
    )
    ticket_source.source.write_back_result(issue_to_investigate.id, result)
    console.print(f"[bold]Updated ticket {issue_to_investigate.url}.[/bold]")


@investigate_app.command()
def github(
    github_url: str = typer.Option(
        "https://api.github.com", help="The GitHub api base url"
    ),
    github_owner: Optional[str] = typer.Option(
        None, help="The GitHub repository owner"
    ),
    github_pat: str = typer.Option(None),
    github_repository: Optional[str] = typer.Option(
        None, help="The GitHub repository name"
    ),
    update: Optional[bool] = typer.Option(False, help="Update GitHub with AI results"),
    github_query: Optional[str] = typer.Option(
        "is:issue is:open", help="Investigate tickets matching a GitHub query"
    ),
    # common options
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    config_file: Optional[Path] = opt_config_file,
    custom_toolsets: Optional[List[Path]] = opt_custom_toolsets,
    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose,
):
    """
    Investigate GitHub issues
    """
    console = init_logging(verbose)
    config = Config.load_from_file(
        config_file,
        api_key=api_key,
        model=model,
        max_steps=max_steps,
        github_url=github_url,
        github_owner=github_owner,
        github_pat=github_pat,
        github_repository=github_repository,
        github_query=github_query,
        custom_toolsets_from_cli=custom_toolsets,
    )
    ai = config.create_console_toolcalling_llm(dal=None, model_name=model)
    source = config.create_github_source()
    try:
        issues = source.fetch_issues()
    except Exception as e:
        logging.error("Failed to fetch issues from GitHub", exc_info=e)
        return

    console.print(
        f"[bold yellow]Analyzing {len(issues)} GitHub issues.[/bold yellow] [red]Press Ctrl+C to stop.[/red]"
    )
    _investigate_issues(
        ai,
        issues,
        config,
        console,
        source=source,
        update=update,
        source_name="GitHub issue",
    )


@investigate_app.command()
def pagerduty(
    pagerduty_api_key: str = typer.Option(None, help="The PagerDuty API key"),
    pagerduty_user_email: Optional[str] = typer.Option(
        None,
        help="When --update is set, which user will be listed as the updater",
    ),
    pagerduty_incident_key: Optional[str] = typer.Option(
        None,
        help="If provided, only analyze a single PagerDuty incident matching this key",
    ),
    update: Optional[bool] = typer.Option(
        False, help="Update PagerDuty with AI results"
    ),
    # common options
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    config_file: Optional[Path] = opt_config_file,
    custom_toolsets: Optional[List[Path]] = opt_custom_toolsets,
    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose,
    json_output_file: Optional[str] = opt_json_output_file,
):
    """
    Investigate PagerDuty incidents
    """
    console = init_logging(verbose)
    config = Config.load_from_file(
        config_file,
        api_key=api_key,
        model=model,
        max_steps=max_steps,
        pagerduty_api_key=pagerduty_api_key,
        pagerduty_user_email=pagerduty_user_email,
        pagerduty_incident_key=pagerduty_incident_key,
        custom_toolsets_from_cli=custom_toolsets,
    )
    ai = config.create_console_toolcalling_llm(dal=None, model_name=model)
    source = config.create_pagerduty_source()
    try:
        issues = source.fetch_issues()
    except Exception as e:
        logging.error("Failed to fetch issues from PagerDuty", exc_info=e)
        return

    console.print(
        f"[bold yellow]Analyzing {len(issues)} PagerDuty incidents.[/bold yellow] [red]Press Ctrl+C to stop.[/red]"
    )
    _investigate_issues(
        ai,
        issues,
        config,
        console,
        source=source,
        update=update,
        json_output_file=json_output_file,
        source_name="PagerDuty incident",
    )


@investigate_app.command()
def opsgenie(
    opsgenie_api_key: str = typer.Option(None, help="The OpsGenie API key"),
    opsgenie_team_integration_key: str = typer.Option(
        None, help=OPSGENIE_TEAM_INTEGRATION_KEY_HELP
    ),
    opsgenie_query: Optional[str] = typer.Option(
        None,
        help="E.g. 'message: Foo' (see https://support.atlassian.com/opsgenie/docs/search-queries-for-alerts/)",
    ),
    update: Optional[bool] = typer.Option(
        False, help="Update OpsGenie with AI results"
    ),
    # common options
    api_key: Optional[str] = opt_api_key,
    model: Optional[str] = opt_model,
    config_file: Optional[Path] = opt_config_file,
    custom_toolsets: Optional[List[Path]] = opt_custom_toolsets,
    max_steps: Optional[int] = opt_max_steps,
    verbose: Optional[List[bool]] = opt_verbose,
):
    """
    Investigate OpsGenie alerts
    """
    console = init_logging(verbose)
    config = Config.load_from_file(
        config_file,
        api_key=api_key,
        model=model,
        max_steps=max_steps,
        opsgenie_api_key=opsgenie_api_key,
        opsgenie_team_integration_key=opsgenie_team_integration_key,
        opsgenie_query=opsgenie_query,
        custom_toolsets_from_cli=custom_toolsets,
    )
    ai = config.create_console_toolcalling_llm(dal=None, model_name=model)
    source = config.create_opsgenie_source()
    try:
        issues = source.fetch_issues()
    except Exception as e:
        logging.error("Failed to fetch issues from OpsGenie", exc_info=e)
        return

    console.print(
        f"[bold yellow]Analyzing {len(issues)} OpsGenie alerts.[/bold yellow] [red]Press Ctrl+C to stop.[/red]"
    )
    _investigate_issues(
        ai,
        issues,
        config,
        console,
        source=source,
        update=update,
        source_name="OpsGenie alert",
    )


@generate_app.command("alertmanager-tests")
def generate_alertmanager_tests(
    alertmanager_url: Optional[str] = typer.Option(None, help="AlertManager url"),
    alertmanager_username: Optional[str] = typer.Option(
        None, help="Username to use for basic auth"
    ),
    alertmanager_password: Optional[str] = typer.Option(
        None, help="Password to use for basic auth"
    ),
    output: Optional[Path] = typer.Option(
        None,
        help="Path to dump alertmanager alerts as json (if not given, output curl commands instead)",
    ),
    config_file: Optional[Path] = opt_config_file,  # type: ignore
    verbose: Optional[List[bool]] = opt_verbose,
):
    """
    Connect to alertmanager and dump all alerts as either a json file or curl commands to simulate the alert (depending on --output flag)
    """
    console = init_logging(verbose)  # type: ignore
    config = Config.load_from_file(
        config_file,
        alertmanager_url=alertmanager_url,
        alertmanager_username=alertmanager_username,
        alertmanager_password=alertmanager_password,
    )

    source = config.create_alertmanager_source()
    if output is None:
        source.output_curl_commands(console)
    else:
        source.dump_raw_alerts_to_file(output)


@toolset_app.command("list")
def list_toolsets(
    verbose: Optional[List[bool]] = opt_verbose,
    config_file: Optional[Path] = opt_config_file,  # type: ignore
):
    """
    List build-in and custom toolsets status of CLI
    """
    console = init_logging(verbose)
    config = Config.load_from_file(config_file)
    cli_toolsets = config.toolset_manager.list_console_toolsets()

    pretty_print_toolset_status(cli_toolsets, console)


@toolset_app.command("refresh")
def refresh_toolsets(
    verbose: Optional[List[bool]] = opt_verbose,
    config_file: Optional[Path] = opt_config_file,  # type: ignore
):
    """
    Refresh build-in and custom toolsets status of CLI
    """
    console = init_logging(verbose)
    config = Config.load_from_file(config_file)
    cli_toolsets = config.toolset_manager.list_console_toolsets(refresh_status=True)
    pretty_print_toolset_status(cli_toolsets, console)


@app.command()
def version() -> None:
    typer.echo(get_version())


def run():
    app()


if __name__ == "__main__":
    run()
