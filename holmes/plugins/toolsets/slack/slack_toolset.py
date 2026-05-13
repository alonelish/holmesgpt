from abc import ABC
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type, cast

from pydantic import Field
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

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
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.pydantic_utils import ToolsetConfig


class SlackToolsetConfig(ToolsetConfig):
    """Configuration for the Slack toolset.

    All fields are optional so the toolset can boot without static config
    when tokens are supplied per-request via
    ``ToolInvokeContext.request_context['runtime_secrets']``.
    """

    bot_token: Optional[str] = Field(
        default=None,
        description=(
            "Slack bot token (xoxb-...). Used as a fallback when "
            "runtime_secrets['slack_bot_token'] is not supplied at "
            "tool-call time."
        ),
        examples=["{{ env.SLACK_TOOLSET_BOT_TOKEN }}"],
    )
    user_token: Optional[str] = Field(
        default=None,
        description=(
            "Slack user token (xoxp-...). Required only for "
            "slack_search_messages. Fallback for "
            "runtime_secrets['slack_user_token']."
        ),
    )
    default_channel: Optional[str] = Field(
        default=None,
        description="Default Slack channel id or name. Informational; tools still require explicit channel params.",
    )
    allow_create_channel: bool = Field(
        default=False,
        description="Permit slack_create_channel. Static-config only — cannot be enabled via runtime_secrets.",
    )
    allow_search_messages: bool = Field(
        default=False,
        description="Permit slack_search_messages. Static-config only — cannot be enabled via runtime_secrets.",
    )


def _runtime_secrets(context: ToolInvokeContext) -> Dict[str, Any]:
    rc = context.request_context or {}
    secrets = rc.get("runtime_secrets") or {}
    if isinstance(secrets, dict):
        return secrets
    return {}


class BaseSlackTool(Tool, ABC):
    """Common token resolution and error handling for Slack tools."""

    def __init__(self, toolset: "SlackToolset", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    def _resolve_bot_token(self, context: ToolInvokeContext) -> Optional[str]:
        token = _runtime_secrets(context).get("slack_bot_token")
        if token:
            return token
        cfg = self._toolset.slack_config
        return cfg.bot_token if cfg else None

    def _resolve_user_token(self, context: ToolInvokeContext) -> Optional[str]:
        token = _runtime_secrets(context).get("slack_user_token")
        if token:
            return token
        cfg = self._toolset.slack_config
        return cfg.user_token if cfg else None

    def _bot_client(
        self, context: ToolInvokeContext, params: dict
    ) -> Tuple[Optional[WebClient], Optional[StructuredToolResult]]:
        token = self._resolve_bot_token(context)
        if not token:
            return None, StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    "No Slack bot token available. Set bot_token in toolset "
                    "config or provide runtime_secrets['slack_bot_token']."
                ),
                params=params,
            )
        return WebClient(token=token), None

    def _user_client(
        self, context: ToolInvokeContext, params: dict
    ) -> Tuple[Optional[WebClient], Optional[StructuredToolResult]]:
        token = self._resolve_user_token(context)
        if not token:
            return None, StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    "No Slack user token available. Set user_token in toolset "
                    "config or provide runtime_secrets['slack_user_token']."
                ),
                params=params,
            )
        return WebClient(token=token), None

    def _slack_error(self, err: SlackApiError, params: dict) -> StructuredToolResult:
        slack_err: Any = None
        try:
            if err.response is not None:
                slack_err = err.response.get("error")
        except Exception:
            slack_err = None
        if not slack_err:
            slack_err = str(err)
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=f"Slack API error: {slack_err}",
            params=params,
        )


def _simplify_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "user": m.get("user") or m.get("bot_id"),
            "text": m.get("text", ""),
            "ts": m.get("ts"),
        }
        for m in (messages or [])
    ]


def _simplify_user(user: Dict[str, Any]) -> Dict[str, Any]:
    profile = user.get("profile") or {}
    return {
        "id": user.get("id"),
        "name": user.get("name"),
        "real_name": user.get("real_name") or profile.get("real_name"),
        "email": profile.get("email"),
    }


class SlackPostMessage(BaseSlackTool):
    def __init__(self, toolset: "SlackToolset"):
        super().__init__(
            toolset=toolset,
            name="slack_post_message",
            description="Post a new message to a Slack channel, optionally as a thread reply.",
            parameters={
                "channel": ToolParameter(
                    description="Channel id (e.g. 'C0123ABCDEF') or name prefixed with '#'.",
                    type="string",
                    required=True,
                ),
                "text": ToolParameter(
                    description="Plain-text message body. Used as fallback when blocks are not rendered.",
                    type="string",
                    required=True,
                ),
                "thread_ts": ToolParameter(
                    description="If posting as a reply, the parent message timestamp.",
                    type="string",
                    required=False,
                ),
                "blocks": ToolParameter(
                    description="Optional Slack Block Kit blocks (array of dicts).",
                    type="array",
                    required=False,
                    items=ToolParameter(type="object", required=False),
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        client, err = self._bot_client(context, params)
        if err is not None:
            return err
        kwargs: Dict[str, Any] = {
            "channel": params["channel"],
            "text": params["text"],
        }
        if params.get("thread_ts"):
            kwargs["thread_ts"] = params["thread_ts"]
        if params.get("blocks"):
            kwargs["blocks"] = params["blocks"]
        try:
            resp = client.chat_postMessage(**kwargs)
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data={"ts": resp.get("ts"), "channel": resp.get("channel")},
                params=params,
            )
        except SlackApiError as e:
            return self._slack_error(e, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: post message to {params.get('channel', '?')}"


class SlackUpdateMessage(BaseSlackTool):
    def __init__(self, toolset: "SlackToolset"):
        super().__init__(
            toolset=toolset,
            name="slack_update_message",
            description="Update an existing Slack message by channel + ts.",
            parameters={
                "channel": ToolParameter(
                    description="Channel id of the message to update.",
                    type="string",
                    required=True,
                ),
                "ts": ToolParameter(
                    description="Timestamp of the message to update (as returned by chat.postMessage).",
                    type="string",
                    required=True,
                ),
                "text": ToolParameter(
                    description="New plain-text message body.",
                    type="string",
                    required=True,
                ),
                "blocks": ToolParameter(
                    description="Optional replacement Slack Block Kit blocks.",
                    type="array",
                    required=False,
                    items=ToolParameter(type="object", required=False),
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        client, err = self._bot_client(context, params)
        if err is not None:
            return err
        kwargs: Dict[str, Any] = {
            "channel": params["channel"],
            "ts": params["ts"],
            "text": params["text"],
        }
        if params.get("blocks"):
            kwargs["blocks"] = params["blocks"]
        try:
            resp = client.chat_update(**kwargs)
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data={"ts": resp.get("ts"), "channel": resp.get("channel")},
                params=params,
            )
        except SlackApiError as e:
            return self._slack_error(e, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: update message {params.get('ts', '?')} in {params.get('channel', '?')}"


class SlackReadThread(BaseSlackTool):
    def __init__(self, toolset: "SlackToolset"):
        super().__init__(
            toolset=toolset,
            name="slack_read_thread",
            description="Read replies in a Slack thread. Returns a simplified [{user, text, ts}, ...] list.",
            parameters={
                "channel": ToolParameter(
                    description="Channel id of the thread.",
                    type="string",
                    required=True,
                ),
                "thread_ts": ToolParameter(
                    description="Timestamp of the parent message of the thread.",
                    type="string",
                    required=True,
                ),
                "limit": ToolParameter(
                    description="Max number of messages to return (default 200).",
                    type="integer",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        client, err = self._bot_client(context, params)
        if err is not None:
            return err
        limit = params.get("limit") or 200
        try:
            resp = client.conversations_replies(
                channel=params["channel"],
                ts=params["thread_ts"],
                limit=limit,
            )
            messages = _simplify_messages(resp.get("messages") or [])
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=messages,
                params=params,
            )
        except SlackApiError as e:
            return self._slack_error(e, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: read thread {params.get('thread_ts', '?')} in {params.get('channel', '?')}"


class SlackReadChannelHistory(BaseSlackTool):
    def __init__(self, toolset: "SlackToolset"):
        super().__init__(
            toolset=toolset,
            name="slack_read_channel_history",
            description="Read recent messages from a Slack channel. Returns simplified [{user, text, ts}, ...].",
            parameters={
                "channel": ToolParameter(
                    description="Channel id to read.",
                    type="string",
                    required=True,
                ),
                "oldest": ToolParameter(
                    description="Only messages after this Unix timestamp (string).",
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(
                    description="Max number of messages to return (default 100).",
                    type="integer",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        client, err = self._bot_client(context, params)
        if err is not None:
            return err
        limit = params.get("limit") or 100
        kwargs: Dict[str, Any] = {"channel": params["channel"], "limit": limit}
        if params.get("oldest"):
            kwargs["oldest"] = params["oldest"]
        try:
            resp = client.conversations_history(**kwargs)
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=_simplify_messages(resp.get("messages") or []),
                params=params,
            )
        except SlackApiError as e:
            return self._slack_error(e, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: read history of {params.get('channel', '?')}"


class SlackCreateChannel(BaseSlackTool):
    def __init__(self, toolset: "SlackToolset"):
        super().__init__(
            toolset=toolset,
            name="slack_create_channel",
            description="Create a new Slack channel. Disabled by default — requires allow_create_channel=true in static toolset config.",
            parameters={
                "name": ToolParameter(
                    description="Channel name (lowercase, no spaces).",
                    type="string",
                    required=True,
                ),
                "is_private": ToolParameter(
                    description="Create as a private channel.",
                    type="boolean",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        cfg = self._toolset.slack_config
        if not cfg or not cfg.allow_create_channel:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Channel creation is disabled. Set allow_create_channel: true in the slack toolset config.",
                params=params,
            )
        client, err = self._bot_client(context, params)
        if err is not None:
            return err
        try:
            resp = client.conversations_create(
                name=params["name"],
                is_private=bool(params.get("is_private", False)),
            )
            channel = resp.get("channel") or {}
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data={"id": channel.get("id"), "name": channel.get("name")},
                params=params,
            )
        except SlackApiError as e:
            return self._slack_error(e, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: create channel {params.get('name', '?')}"


class SlackInviteUsers(BaseSlackTool):
    def __init__(self, toolset: "SlackToolset"):
        super().__init__(
            toolset=toolset,
            name="slack_invite_users",
            description="Invite one or more users to a Slack channel.",
            parameters={
                "channel": ToolParameter(
                    description="Channel id to invite users to.",
                    type="string",
                    required=True,
                ),
                "user_ids": ToolParameter(
                    description="List of Slack user IDs to invite.",
                    type="array",
                    required=True,
                    items=ToolParameter(type="string", required=True),
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        user_ids = params.get("user_ids") or []
        if not user_ids:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="user_ids must be a non-empty list.",
                params=params,
            )
        client, err = self._bot_client(context, params)
        if err is not None:
            return err
        try:
            resp = client.conversations_invite(
                channel=params["channel"],
                users=",".join(user_ids),
            )
            channel = resp.get("channel") or {}
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data={"id": channel.get("id"), "name": channel.get("name")},
                params=params,
            )
        except SlackApiError as e:
            return self._slack_error(e, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: invite {len(params.get('user_ids') or [])} user(s) to {params.get('channel', '?')}"


class SlackLookupUserByEmail(BaseSlackTool):
    def __init__(self, toolset: "SlackToolset"):
        super().__init__(
            toolset=toolset,
            name="slack_lookup_user_by_email",
            description="Look up a Slack user by email address.",
            parameters={
                "email": ToolParameter(
                    description="Email address of the user.",
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        client, err = self._bot_client(context, params)
        if err is not None:
            return err
        try:
            resp = client.users_lookupByEmail(email=params["email"])
            user = resp.get("user") or {}
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=_simplify_user(user),
                params=params,
            )
        except SlackApiError as e:
            return self._slack_error(e, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: lookup user by email {params.get('email', '?')}"


class SlackGetUserInfo(BaseSlackTool):
    def __init__(self, toolset: "SlackToolset"):
        super().__init__(
            toolset=toolset,
            name="slack_get_user_info",
            description="Get info about a Slack user by id.",
            parameters={
                "user_id": ToolParameter(
                    description="Slack user id (e.g. 'U0123ABCDEF').",
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        client, err = self._bot_client(context, params)
        if err is not None:
            return err
        try:
            resp = client.users_info(user=params["user_id"])
            user = resp.get("user") or {}
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=_simplify_user(user),
                params=params,
            )
        except SlackApiError as e:
            return self._slack_error(e, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: get user {params.get('user_id', '?')}"


class SlackSearchMessages(BaseSlackTool):
    def __init__(self, toolset: "SlackToolset"):
        super().__init__(
            toolset=toolset,
            name="slack_search_messages",
            description=(
                "Search Slack messages. Disabled by default — requires "
                "allow_search_messages=true in static config AND a user "
                "token (xoxp-...)."
            ),
            parameters={
                "query": ToolParameter(
                    description="Slack search query (same syntax as the Slack search box).",
                    type="string",
                    required=True,
                ),
                "count": ToolParameter(
                    description="Max results to return (default 20).",
                    type="integer",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        cfg = self._toolset.slack_config
        if not cfg or not cfg.allow_search_messages:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Message search is disabled. Set allow_search_messages: true in the slack toolset config.",
                params=params,
            )
        client, err = self._user_client(context, params)
        if err is not None:
            return err
        try:
            resp = client.search_messages(
                query=params["query"],
                count=params.get("count") or 20,
            )
            messages = (resp.get("messages") or {}).get("matches") or []
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=_simplify_messages(messages),
                params=params,
            )
        except SlackApiError as e:
            return self._slack_error(e, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: search messages {params.get('query', '')!r}"


class SlackToolset(Toolset):
    config_classes: ClassVar[List[Type[SlackToolsetConfig]]] = [SlackToolsetConfig]

    def __init__(self):
        super().__init__(
            name="slack",
            description="Read channels, post messages and threads, and look up users in Slack.",
            icon_url="https://a.slack-edge.com/80588/marketing/img/meta/slack_hash_256.png",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/slack/",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                SlackPostMessage(self),
                SlackUpdateMessage(self),
                SlackReadThread(self),
                SlackReadChannelHistory(self),
                SlackCreateChannel(self),
                SlackInviteUsers(self),
                SlackLookupUserByEmail(self),
                SlackGetUserInfo(self),
                SlackSearchMessages(self),
            ],
            tags=[ToolsetTag.CORE],
            enabled=False,
        )

    @property
    def slack_config(self) -> Optional[SlackToolsetConfig]:
        return cast(Optional[SlackToolsetConfig], self.config)

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        try:
            self.config = SlackToolsetConfig(**(config or {}))
        except Exception as e:
            return False, f"Invalid Slack config: {e}"

        cfg = self.slack_config
        if not cfg or not cfg.bot_token:
            return True, (
                "Slack toolset loaded without static bot_token. "
                "Per-request runtime_secrets['slack_bot_token'] is required "
                "at tool-call time."
            )

        try:
            WebClient(token=cfg.bot_token).auth_test()
            return True, "Slack auth OK"
        except SlackApiError as e:
            slack_err = None
            try:
                if e.response is not None:
                    slack_err = e.response.get("error")
            except Exception:
                slack_err = None
            return False, f"Slack auth_test failed: {slack_err or e}"
        except Exception as e:
            return False, f"Slack auth_test failed: {e}"

    def get_example_config(self) -> Dict[str, Any]:
        return {
            "bot_token": "{{ env.SLACK_TOOLSET_BOT_TOKEN }}",
            "user_token": "{{ env.SLACK_TOOLSET_USER_TOKEN }}",
            "default_channel": "#alerts",
            "allow_create_channel": False,
            "allow_search_messages": False,
        }
