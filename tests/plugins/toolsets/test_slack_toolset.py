from unittest.mock import MagicMock

from slack_sdk.errors import SlackApiError

from holmes.core.llm import LLM
from holmes.core.tools import (
    StructuredToolResultStatus,
    ToolInvokeContext,
)
from holmes.plugins.toolsets.slack import slack_toolset as st
from holmes.plugins.toolsets.slack.slack_toolset import (
    SlackToolset,
    SlackToolsetConfig,
)


def _ctx(request_context=None):
    return ToolInvokeContext.model_construct(
        tool_number=1,
        user_approved=False,
        llm=MagicMock(spec=LLM),
        max_token_count=1000,
        tool_call_id="c1",
        tool_name="t",
        request_context=request_context,
    )


def _toolset(config=None):
    ts = SlackToolset()
    ts.config = SlackToolsetConfig(**(config or {}))
    return ts


def _slack_api_error(code: str = "not_authed") -> SlackApiError:
    return SlackApiError(message=code, response={"error": code})


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def test_resolve_bot_token_runtime_secret_wins_over_static_config():
    tool = st.SlackPostMessage(_toolset({"bot_token": "static-token"}))
    ctx = _ctx({"runtime_secrets": {"slack_bot_token": "runtime-token"}})
    assert tool._resolve_bot_token(ctx) == "runtime-token"


def test_resolve_bot_token_falls_back_to_static_when_no_runtime_secret():
    tool = st.SlackPostMessage(_toolset({"bot_token": "static-token"}))
    ctx = _ctx({"headers": {"X-Tenant-Id": "t1"}})
    assert tool._resolve_bot_token(ctx) == "static-token"


def test_bot_client_returns_error_result_with_no_token_available():
    tool = st.SlackPostMessage(_toolset({}))
    client, err = tool._bot_client(_ctx(), params={"channel": "C0"})
    assert client is None
    assert err is not None
    assert err.status == StructuredToolResultStatus.ERROR
    assert "No Slack bot token" in (err.error or "")


# ---------------------------------------------------------------------------
# Post message
# ---------------------------------------------------------------------------


def test_post_message_happy_path(monkeypatch):
    tool = st.SlackPostMessage(_toolset({"bot_token": "xoxb-static"}))

    mock_client = MagicMock()
    mock_client.chat_postMessage.return_value = {"ts": "1.234", "channel": "C0"}
    webclient_ctor = MagicMock(return_value=mock_client)
    monkeypatch.setattr(st, "WebClient", webclient_ctor)

    result = tool._invoke(
        {"channel": "#alerts", "text": "hello"},
        _ctx({"runtime_secrets": {"slack_bot_token": "xoxb-runtime"}}),
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data == {"ts": "1.234", "channel": "C0"}
    mock_client.chat_postMessage.assert_called_once_with(
        channel="#alerts", text="hello"
    )
    # Runtime secret was used, not static config.
    webclient_ctor.assert_called_once_with(token="xoxb-runtime")


def test_post_message_slack_api_error_returns_error_without_leaking_token(monkeypatch):
    tool = st.SlackPostMessage(_toolset({"bot_token": "xoxb-static-SECRET"}))

    mock_client = MagicMock()
    mock_client.chat_postMessage.side_effect = _slack_api_error("channel_not_found")
    monkeypatch.setattr(st, "WebClient", MagicMock(return_value=mock_client))

    result = tool._invoke({"channel": "#nope", "text": "hi"}, _ctx())

    assert result.status == StructuredToolResultStatus.ERROR
    assert "channel_not_found" in (result.error or "")
    assert "xoxb-static-SECRET" not in (result.error or "")


# ---------------------------------------------------------------------------
# Read thread / channel history
# ---------------------------------------------------------------------------


def test_read_thread_returns_simplified_messages(monkeypatch):
    tool = st.SlackReadThread(_toolset({"bot_token": "xoxb"}))

    mock_client = MagicMock()
    mock_client.conversations_replies.return_value = {
        "messages": [
            {"user": "U1", "text": "hi", "ts": "1.0", "team": "T1"},
            {"user": "U2", "text": "yo", "ts": "1.5", "blocks": [{"a": 1}]},
        ]
    }
    monkeypatch.setattr(st, "WebClient", MagicMock(return_value=mock_client))

    result = tool._invoke({"channel": "C0", "thread_ts": "1.0"}, _ctx())

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data == [
        {"user": "U1", "text": "hi", "ts": "1.0"},
        {"user": "U2", "text": "yo", "ts": "1.5"},
    ]
    mock_client.conversations_replies.assert_called_once_with(
        channel="C0", ts="1.0", limit=200
    )


# ---------------------------------------------------------------------------
# Create channel (double-gated)
# ---------------------------------------------------------------------------


def test_create_channel_disabled_by_default_and_does_not_call_api(monkeypatch):
    tool = st.SlackCreateChannel(_toolset({"bot_token": "xoxb"}))
    webclient_ctor = MagicMock()
    monkeypatch.setattr(st, "WebClient", webclient_ctor)

    result = tool._invoke({"name": "new-channel"}, _ctx())

    assert result.status == StructuredToolResultStatus.ERROR
    assert "disabled" in (result.error or "").lower()
    webclient_ctor.assert_not_called()


def test_create_channel_enabled_creates_private(monkeypatch):
    tool = st.SlackCreateChannel(
        _toolset({"bot_token": "xoxb", "allow_create_channel": True})
    )

    mock_client = MagicMock()
    mock_client.conversations_create.return_value = {
        "channel": {"id": "C9", "name": "new"}
    }
    monkeypatch.setattr(st, "WebClient", MagicMock(return_value=mock_client))

    result = tool._invoke({"name": "new", "is_private": True}, _ctx())

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data == {"id": "C9", "name": "new"}
    mock_client.conversations_create.assert_called_once_with(
        name="new", is_private=True
    )


# ---------------------------------------------------------------------------
# Search messages (double-gated, user token)
# ---------------------------------------------------------------------------


def test_search_messages_disabled_by_default():
    tool = st.SlackSearchMessages(
        _toolset({"bot_token": "xoxb", "user_token": "xoxp"})
    )
    result = tool._invoke({"query": "error"}, _ctx())
    assert result.status == StructuredToolResultStatus.ERROR
    assert "disabled" in (result.error or "").lower()


def test_search_messages_requires_user_token_when_allowed():
    tool = st.SlackSearchMessages(
        _toolset({"bot_token": "xoxb", "allow_search_messages": True})
    )
    result = tool._invoke({"query": "error"}, _ctx())
    assert result.status == StructuredToolResultStatus.ERROR
    assert "user token" in (result.error or "").lower()


def test_search_messages_uses_user_token_when_allowed(monkeypatch):
    tool = st.SlackSearchMessages(
        _toolset({"allow_search_messages": True, "user_token": "xoxp-user"})
    )

    mock_client = MagicMock()
    mock_client.search_messages.return_value = {
        "messages": {"matches": [{"user": "U1", "text": "found", "ts": "1.0"}]}
    }
    webclient_ctor = MagicMock(return_value=mock_client)
    monkeypatch.setattr(st, "WebClient", webclient_ctor)

    result = tool._invoke({"query": "found"}, _ctx())

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data == [{"user": "U1", "text": "found", "ts": "1.0"}]
    webclient_ctor.assert_called_once_with(token="xoxp-user")


# ---------------------------------------------------------------------------
# Invite users
# ---------------------------------------------------------------------------


def test_invite_users_rejects_empty_list():
    tool = st.SlackInviteUsers(_toolset({"bot_token": "xoxb"}))
    result = tool._invoke({"channel": "C0", "user_ids": []}, _ctx())
    assert result.status == StructuredToolResultStatus.ERROR
    assert "non-empty" in (result.error or "").lower()


def test_invite_users_joins_ids_with_comma(monkeypatch):
    tool = st.SlackInviteUsers(_toolset({"bot_token": "xoxb"}))

    mock_client = MagicMock()
    mock_client.conversations_invite.return_value = {
        "channel": {"id": "C0", "name": "x"}
    }
    monkeypatch.setattr(st, "WebClient", MagicMock(return_value=mock_client))

    tool._invoke(
        {"channel": "C0", "user_ids": ["U1", "U2", "U3"]}, _ctx()
    )

    mock_client.conversations_invite.assert_called_once_with(
        channel="C0", users="U1,U2,U3"
    )


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------


def test_prerequisites_no_static_token_loads_in_runtime_mode():
    ok, msg = SlackToolset().prerequisites_callable({})
    assert ok is True
    assert "runtime_secrets" in msg


def test_prerequisites_invalid_token_fails(monkeypatch):
    mock_client = MagicMock()
    mock_client.auth_test.side_effect = _slack_api_error("invalid_auth")
    monkeypatch.setattr(st, "WebClient", MagicMock(return_value=mock_client))

    ok, msg = SlackToolset().prerequisites_callable({"bot_token": "xoxb-bad"})
    assert ok is False
    assert "invalid_auth" in msg


def test_prerequisites_valid_token_succeeds(monkeypatch):
    mock_client = MagicMock()
    mock_client.auth_test.return_value = {"ok": True}
    monkeypatch.setattr(st, "WebClient", MagicMock(return_value=mock_client))

    ok, msg = SlackToolset().prerequisites_callable({"bot_token": "xoxb-good"})
    assert ok is True
    assert "Slack auth OK" in msg
