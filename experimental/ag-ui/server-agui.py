# ruff: noqa: E402
import os

from holmes.utils.cert_utils import add_custom_certificate

ADDITIONAL_CERTIFICATE: str = os.environ.get("CERTIFICATE", "")
if add_custom_certificate(ADDITIONAL_CERTIFICATE):
    print("added custom certificate")

# DO NOT ADD ANY IMPORTS OR CODE ABOVE THIS LINE
# IMPORTING ABOVE MIGHT INITIALIZE AN HTTPS CLIENT THAT DOESN'T TRUST THE CUSTOM CERTIFICATE

# Safe to import networked libs below
import json
import logging
import time
import uuid

import colorlog
import litellm
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.responses import PlainTextResponse

from holmes.common.env_vars import (
    HOLMES_HOST,
    HOLMES_PORT,
)
from holmes.config import Config
from holmes.core.conversations import (
    build_chat_messages,
)
from holmes.core.models import (
    ChatRequest,
    FollowUpAction,
)
from holmes.core.prompt import PromptComponent
from holmes.core.tools_utils.filesystem_result_storage import tool_result_storage
from holmes.utils.stream import StreamEvents, StreamMessage

from ag_ui.core import (
    AssistantMessage,
    BinaryInputContent,
    CustomEvent,
    EventType,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder


def init_logging():
    logging_level = os.environ.get("LOG_LEVEL", "INFO")
    logging_format = "%(log_color)s%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s"
    logging_datefmt = "%Y-%m-%d %H:%M:%S"

    print("setting up colored logging")
    colorlog.basicConfig(
        format=logging_format, level=logging_level, datefmt=logging_datefmt
    )
    logging.getLogger().setLevel(logging_level)

    httpx_logger = logging.getLogger("httpx")
    if httpx_logger:
        httpx_logger.setLevel(logging.WARNING)

    logging.info(f"logger initialized using {logging_level} log level")


init_logging()
config = Config.load_from_env()
dal = config.dal

app = FastAPI()

# Add CORS middleware front-end access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _extract_passthrough_headers(request: Request) -> dict:
    """Extract pass-through headers, excluding sensitive auth headers."""
    blocked_headers_str = os.environ.get(
        "HOLMES_PASSTHROUGH_BLOCKED_HEADERS", "authorization,cookie,set-cookie"
    )
    blocked_headers = {
        h.strip().lower() for h in blocked_headers_str.split(",") if h.strip()
    }
    passthrough_headers = {}
    for header_name, header_value in request.headers.items():
        if header_name.lower() not in blocked_headers:
            passthrough_headers[header_name] = header_value
    return {"headers": passthrough_headers} if passthrough_headers else {}


def _already_answered(conversation_history: list[dict] | None) -> bool:
    if conversation_history is None:
        return False
    return any(msg["role"] == "assistant" for msg in conversation_history)


@app.get("/api/agui/chat/health")
def agui_chat_health(request: Request):
    return JSONResponse(content="ok")


@app.post("/api/agui/chat")
def agui_chat(input_data: RunAgentInput, request: Request):
    accept_header = request.headers.get("accept", "")
    encoder = EventEncoder(accept=accept_header)

    logging.debug(f"AG-UI context: {input_data.context}")
    logging.debug(f"AG-UI state: {input_data.state}")

    chat_request = _agui_input_to_holmes_chat_request(input_data=input_data)
    if not chat_request.ask:
        return PlainTextResponse(
            "Bad request. Chat message cannot be empty", status_code=400
        )

    # --- Behavior controls from forwarded_props ---
    prompt_component_overrides = None
    forwarded = input_data.forwarded_props or {}
    behavior_controls = forwarded.get("behavior_controls") if isinstance(forwarded, dict) else None
    if behavior_controls and isinstance(behavior_controls, dict):
        prompt_component_overrides = {}
        for k, v in behavior_controls.items():
            try:
                prompt_component_overrides[PromptComponent(k.lower())] = v
            except ValueError:
                logging.warning(f"Unknown behavior_controls key '{k}', ignoring")

    # --- Tool result storage ---
    storage = tool_result_storage()
    tool_results_dir = storage.__enter__()

    # --- Passthrough headers ---
    request_context = _extract_passthrough_headers(request)

    # --- Runbooks ---
    runbooks = config.get_runbook_catalog()

    # --- Images ---
    images = _extract_images(input_data)

    # --- Follow-up actions ---
    follow_up_actions = []
    if not _already_answered(chat_request.conversation_history):
        follow_up_actions = [
            FollowUpAction(
                id="logs",
                action_label="Logs",
                prompt="Show me the relevant logs",
                pre_action_notification_text="Fetching relevant logs...",
            ),
            FollowUpAction(
                id="graphs",
                action_label="Graphs",
                prompt="Show me the relevant graphs. Use prometheus and make sure you embed the results with `<< >>` to display a graph",
                pre_action_notification_text="Drawing some graphs...",
            ),
            FollowUpAction(
                id="articles",
                action_label="Articles",
                prompt="List the relevant runbooks and links used. Write a short summary for each",
                pre_action_notification_text="Looking up and summarizing runbooks and links...",
            ),
        ]

    ai = config.create_agui_toolcalling_llm(
        dal=dal, model=chat_request.model, tool_results_dir=tool_results_dir
    )
    global_instructions = dal.get_global_instructions_for_account()
    messages = build_chat_messages(
        chat_request.ask,
        chat_request.conversation_history,
        ai=ai,
        config=config,
        global_instructions=global_instructions,
        additional_system_prompt=chat_request.additional_system_prompt,
        runbooks=runbooks,
        images=images,
        prompt_component_overrides=prompt_component_overrides,
    )

    req_info = f"/api/agui/chat request: ask={chat_request.ask}"

    async def event_generator(message_history):
        try:
            yield encoder.encode(
                RunStartedEvent(
                    type=EventType.RUN_STARTED,
                    thread_id=input_data.thread_id,
                    run_id=input_data.run_id,
                )
            )

            # Emit initial state snapshot with follow-up actions
            yield encoder.encode(
                StateSnapshotEvent(
                    type=EventType.STATE_SNAPSHOT,
                    snapshot={
                        "follow_up_actions": [f.model_dump() for f in follow_up_actions],
                    },
                )
            )

            hgpt_chat_stream_response: StreamMessage = ai.call_stream(
                msgs=message_history,
                enable_tool_approval=chat_request.enable_tool_approval or False,
                tool_decisions=chat_request.tool_decisions,
                response_format=chat_request.response_format,
                request_context=request_context,
            )
            for chunk in hgpt_chat_stream_response:
                if hasattr(chunk, "event"):
                    event_type = (
                        chunk.event.value
                        if hasattr(chunk.event, "value")
                        else str(chunk.event)
                    )
                    logging.debug(f"Streaming chunk: {event_type}")
                else:
                    event_type = "unknown"
                    logging.debug(f"Streaming chunk: {chunk}")

                if not hasattr(chunk, "data"):
                    continue

                tool_name = chunk.data.get(
                    "tool_name", chunk.data.get("name", "Tool")
                )

                if event_type in (
                    StreamEvents.AI_MESSAGE,
                    StreamEvents.ANSWER_END,
                    "unknown",
                ):
                    content = str(chunk.data.get("content", ""))
                    async for event in _stream_agui_text_message_event(
                        message=content
                    ):
                        yield encoder.encode(event)

                    # For final answer, emit metadata as custom event and state snapshot
                    if event_type == StreamEvents.ANSWER_END:
                        metadata = chunk.data.get("metadata") or {}
                        if metadata:
                            yield encoder.encode(
                                CustomEvent(
                                    type=EventType.CUSTOM,
                                    name="metadata",
                                    value=metadata,
                                )
                            )
                        # Emit final state with conversation history
                        yield encoder.encode(
                            StateSnapshotEvent(
                                type=EventType.STATE_SNAPSHOT,
                                snapshot={
                                    "follow_up_actions": [f.model_dump() for f in follow_up_actions],
                                    "metadata": metadata,
                                    "conversation_history": chunk.data.get("messages"),
                                },
                            )
                        )

                elif event_type == StreamEvents.START_TOOL:
                    yield encoder.encode(
                        StepStartedEvent(
                            type=EventType.STEP_STARTED,
                            step_name=tool_name,
                        )
                    )
                    async for event in _stream_agui_text_message_event(
                        message=f"Using Agent tool: `{tool_name}`..."
                    ):
                        yield encoder.encode(event)

                elif event_type == StreamEvents.TOOL_RESULT:
                    logging.debug(
                        f"TOOL_RESULT received - tool_name: {tool_name}"
                    )

                    # Emit step finished for tool
                    yield encoder.encode(
                        StepFinishedEvent(
                            type=EventType.STEP_FINISHED,
                            step_name=tool_name,
                        )
                    )

                    front_end_tool_invoked = False
                    if _should_graph_timeseries_data(tool_name=tool_name):
                        front_end_tool_invoked = True
                        ts_data = _parse_timeseries_data(chunk.data)
                        tool_call_id = chunk.data.get(
                            "tool_call_id", chunk.data.get("id", "unknown")
                        )
                        async for tool_event in _invoke_front_end_tool(
                            tool_call_id=tool_call_id,
                            tool_call_name="graph_timeseries_data",
                            tool_call_args=ts_data,
                        ):
                            yield encoder.encode(tool_event)
                    if _should_execute_suggested_query(
                        backend_tool_name=tool_name, frontend_tools=input_data.tools
                    ):
                        front_end_tool_invoked = True
                        tool_call_id = chunk.data.get(
                            "tool_call_id", chunk.data.get("id", "unknown")
                        )
                        front_end_query_tool = None
                        if tool_name == "opensearch_ppl_query_assist":
                            front_end_query_tool = "execute_ppl_query"
                        elif tool_name in (
                            "execute_prometheus_range_query",
                            "execute_prometheus_instant_query",
                        ):
                            front_end_query_tool = "execute_promql_query"

                        async for tool_event in _invoke_front_end_tool(
                            tool_call_id=tool_call_id,
                            tool_call_name=front_end_query_tool,
                            tool_call_args={"query": _parse_query(chunk.data)},
                        ):
                            yield encoder.encode(tool_event)
                    if not front_end_tool_invoked:
                        if tool_name == "TodoWrite":
                            tool_message = _format_todo_write(data=chunk.data)
                        else:
                            tool_message = f"{tool_name} result:\n{chunk.data.get('result', {}).get('data', '')[0:200]}..."

                        async for event in _stream_agui_text_message_event(
                            message=tool_message
                        ):
                            yield encoder.encode(event)

                elif event_type == StreamEvents.APPROVAL_REQUIRED:
                    # Emit tool approval request as a front-end tool invocation.
                    # The front-end should render approval UI and return results as tool messages.
                    pending_approvals = chunk.data.get("pending_approvals", [])
                    for approval in pending_approvals:
                        approval_tool_call_id = f"approval-{approval.get('tool_call_id', str(uuid.uuid4()))}"
                        async for tool_event in _invoke_front_end_tool(
                            tool_call_id=approval_tool_call_id,
                            tool_call_name="holmes_tool_approval",
                            tool_call_args={
                                "tool_call_id": approval.get("tool_call_id"),
                                "tool_name": approval.get("tool_name"),
                                "description": approval.get("description"),
                                "params": approval.get("params", {}),
                            },
                        ):
                            yield encoder.encode(tool_event)

                    # Also emit as text so the user sees what's pending
                    approval_names = [a.get("tool_name", "unknown") for a in pending_approvals]
                    async for event in _stream_agui_text_message_event(
                        message=f"Awaiting approval for: {', '.join(approval_names)}"
                    ):
                        yield encoder.encode(event)

                elif event_type == StreamEvents.TOKEN_COUNT:
                    metadata = chunk.data.get("metadata", {})
                    yield encoder.encode(
                        CustomEvent(
                            type=EventType.CUSTOM,
                            name="token_count",
                            value=metadata,
                        )
                    )

                elif event_type == StreamEvents.CONVERSATION_HISTORY_COMPACTED:
                    yield encoder.encode(
                        CustomEvent(
                            type=EventType.CUSTOM,
                            name="conversation_history_compacted",
                            value=chunk.data,
                        )
                    )

            yield encoder.encode(
                RunFinishedEvent(
                    type=EventType.RUN_FINISHED,
                    thread_id=input_data.thread_id,
                    run_id=input_data.run_id,
                )
            )
        except litellm.exceptions.AuthenticationError as e:
            logging.error(f"Authentication error in /api/agui/chat: {e}", exc_info=True)
            yield encoder.encode(
                RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    message=f"Authentication error: {str(e)}",
                )
            )
        except litellm.exceptions.RateLimitError as e:
            logging.error(f"Rate limit error in /api/agui/chat: {e}", exc_info=True)
            yield encoder.encode(
                RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    message=f"Rate limit exceeded: {str(e)}",
                )
            )
        except Exception as e:
            logging.error(f"Error in /api/agui/chat: {e}", exc_info=True)
            yield encoder.encode(
                RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    message=f"Agent encountered an error: {str(e)}",
                )
            )
        finally:
            logging.info(f"Stream request end: {req_info}")
            storage.__exit__(None, None, None)

    return StreamingResponse(
        event_generator(messages), media_type=encoder.get_content_type()
    )


def _extract_images(input_data: RunAgentInput) -> list[str | dict] | None:
    """Extract images from AG-UI UserMessage BinaryInputContent."""
    images = []
    for msg in input_data.messages:
        if msg.role != "user":
            continue
        # UserMessage content can be a string or list of TextInputContent/BinaryInputContent
        if not isinstance(msg.content, list):
            continue
        for part in msg.content:
            if not isinstance(part, BinaryInputContent):
                continue
            if not part.mime_type.startswith("image/"):
                continue
            if part.data:
                # base64 data URI
                images.append({
                    "url": f"data:{part.mime_type};base64,{part.data}",
                    "format": part.mime_type,
                })
            elif part.url:
                images.append({
                    "url": part.url,
                    "format": part.mime_type,
                })
    return images if images else None


def _format_todo_write(data) -> str:
    status_icons = {"pending": "⬜", "in_progress": "⏳", "completed": "✅"}
    result_data = data.get("result", {})
    params = result_data.get("params", {})
    todos = params.get("todos", {})
    output_str = "### Investigation Tasks:  \n"
    task_list = []
    for idx, todo in enumerate(todos):
        status = todo.get("status", "")
        icon = status_icons.get(status, "⬜")
        content = todo.get("content", "")
        task_list.append(f"{idx+1}. {icon} - {content}")
    output_str += "  \n".join(task for task in task_list)
    return output_str


def _should_execute_suggested_query(
    backend_tool_name: str, frontend_tools: list
) -> bool:
    for fe_tool in frontend_tools:
        if "execute_prom" in fe_tool.name and backend_tool_name in (
            "execute_prometheus_range_query",
            "execute_prometheus_instant_query",
        ):
            return True
        elif (
            "execute_ppl" in fe_tool.name
            and backend_tool_name == "opensearch_ppl_query_assist"
        ):
            return True
    return False


def _parse_query(data) -> str:
    result_data = data.get("result", {})
    params = result_data.get("params", {})
    query = params.get("query", "")
    return query


def _should_graph_timeseries_data(tool_name: str) -> bool:
    return tool_name in (
        "execute_prometheus_range_query",
        "execute_prometheus_instant_query",
    )


def _parse_timeseries_data(data) -> dict:
    try:
        result_data = data.get("result", {})
        params = result_data.get("params", {})
        query = params.get("query", "")
        description = params.get("description")

        if isinstance(result_data, str):
            try:
                result_data = json.loads(result_data)
            except json.JSONDecodeError:
                logging.warning(f"Failed to parse result as JSON: {result_data}")
                result_data = {}

        prometheus_data = result_data
        result_type = "unknown"
        if "data" in result_data:
            prometheus_data = json.loads(result_data["data"]).get("data")
            result_type = prometheus_data.get("resultType", "unknown")

        metadata = {
            "timestamp": int(time.time()),
            "source": "Prometheus",
            "result_type": result_type,
            "description": description,
            "query": query,
        }

        return {
            "title": description,
            "query": query,
            "data": prometheus_data,
            "metadata": metadata,
        }

    except Exception as e:
        logging.error(f"Error parsing timeseries data: {e}", exc_info=True)
        return {
            "title": "Prometheus Query Results (Parse Error)",
            "query": data.get("query", ""),
            "data": {"result": []},
            "metadata": {
                "timestamp": int(time.time()),
                "source": "Prometheus",
                "error": str(e),
            },
        }


async def _invoke_front_end_tool(
    tool_call_id: str, tool_call_name: str, tool_call_args: dict
):
    yield ToolCallStartEvent(
        type=EventType.TOOL_CALL_START,
        tool_call_id=tool_call_id,
        tool_call_name=tool_call_name,
    )
    yield ToolCallArgsEvent(
        type=EventType.TOOL_CALL_ARGS,
        tool_call_id=tool_call_id,
        delta=json.dumps(tool_call_args),
    )
    yield ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=tool_call_id)


async def _stream_agui_text_message_event(message: str):
    message_id = str(uuid.uuid4())
    yield TextMessageStartEvent(
        type=EventType.TEXT_MESSAGE_START, message_id=message_id, role="assistant"
    )
    yield TextMessageContentEvent(
        type=EventType.TEXT_MESSAGE_CONTENT, message_id=message_id, delta=message
    )
    yield TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=message_id)


def _agui_input_to_holmes_chat_request(input_data: RunAgentInput) -> ChatRequest:
    """Convert AG-UI RunAgentInput to HolmesGPT ChatRequest format."""
    non_system_messages = []
    # Store front-end "tool" messages as assistant messages in conversation history.
    # Full tool result integration requires matching toolUse/toolResult pairs in the
    # conversation which varies by LLM provider.
    for msg in input_data.messages:
        if msg.role in ("user", "assistant"):
            non_system_messages.append(msg)
        elif msg.role == "tool":
            non_system_messages.append(AssistantMessage(content=msg.content, id=msg.id))

    conversation_history = [
        {
            "role": "system",
            "content": "You are Holmes, an AI assistant for observability. You use Prometheus metrics, alerts and OpenSearch logs to quickly perform root cause analysis.",
        }
    ]
    if len(non_system_messages) > 1:
        conversation_history.extend(
            [
                {
                    "role": msg.role,
                    "content": _get_text_content(msg).strip() if _get_text_content(msg) else "",
                }
                for msg in non_system_messages[:-1]
            ]
        )

    # Get the last user message and validate it
    last_user_message = ""
    if non_system_messages and non_system_messages[-1].role == "user":
        content = _get_text_content(non_system_messages[-1])
        last_user_message = content.strip() if content else ""

    if input_data.context:
        # Insert page context near the end so it stays fresh and isn't buried.
        context_parts = []
        for ctx in input_data.context:
            context_parts.append(f"{ctx.description}: {ctx.value}")
        if context_parts:
            conversation_history.append(
                {
                    "role": "system",
                    "content": f"The user has the following information in their current web page for which you are assisting them. {' '.join(context_parts)}",
                },
            )

    # Extract behavior_controls and response_format from forwarded_props
    forwarded = input_data.forwarded_props or {}
    behavior_controls = None
    response_format = None
    additional_system_prompt = None
    if isinstance(forwarded, dict):
        behavior_controls = forwarded.get("behavior_controls")
        response_format = forwarded.get("response_format")
        additional_system_prompt = forwarded.get("additional_system_prompt")

    chat_request = ChatRequest(
        ask=last_user_message,
        conversation_history=conversation_history,
        model=getattr(input_data, "model", None),
        stream=True,
        behavior_controls=behavior_controls,
        response_format=response_format,
        additional_system_prompt=additional_system_prompt,
    )
    return chat_request


def _get_text_content(msg) -> str:
    """Extract text content from a message, handling both string and multimodal content."""
    if isinstance(msg.content, str):
        return msg.content
    if isinstance(msg.content, list):
        text_parts = []
        for part in msg.content:
            if hasattr(part, "text"):
                text_parts.append(part.text)
            elif hasattr(part, "type") and part.type == "text":
                text_parts.append(part.text)
        return " ".join(text_parts)
    return str(msg.content) if msg.content else ""


@app.get("/api/model")
def get_model():
    return {"model_name": config.get_models_list()}


if __name__ == "__main__":
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["access"]["fmt"] = (
        "%(asctime)s %(levelname)-8s %(message)s"
    )
    log_config["formatters"]["default"]["fmt"] = (
        "%(asctime)s %(levelname)-8s %(message)s"
    )
    uvicorn.run(
        app, host=HOLMES_HOST, port=HOLMES_PORT, log_config=log_config, reload=False
    )
