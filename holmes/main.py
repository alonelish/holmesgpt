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
import socket
import uuid
from pathlib import Path
from typing import List, Optional

import typer
from holmes import get_version  # type: ignore
from holmes.config import (
    DEFAULT_CONFIG_LOCATION,
    Config,
)
from holmes.core.prompt import build_initial_ask_messages
from holmes.core.resource_instruction import ResourceInstructionDocument
from holmes.core.tools import pretty_print_toolset_status
from holmes.core.tracing import SpanType, TracingFactory
from holmes.interactive import run_interactive_loop
from holmes.plugins.destinations import DestinationType
from holmes.plugins.interfaces import Issue
from holmes.utils.console.logging import init_logging
from holmes.utils.console.result import handle_result
from holmes.utils.file_utils import write_json_file

app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)


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
