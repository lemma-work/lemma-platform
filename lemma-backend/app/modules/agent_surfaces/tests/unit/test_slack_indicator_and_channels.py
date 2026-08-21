from __future__ import annotations

import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService

pytestmark = pytest.mark.asyncio


def _dm_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="D1",
        external_thread_id="100.0",
        external_message_id="100.0",
        message_text="hi",
        is_dm=True,
        reply_target={"channel": "D1", "thread_ts": "100.0"},
    )


def _api_error(code: str) -> SlackApiError:
    class _Response(dict):
        pass

    return SlackApiError(message=code, response=_Response({"error": code}))


@pytest.mark.parametrize(
    "error_code",
    ["missing_scope", "invalid_arguments", "method_not_supported_for_channel_type"],
)
async def test_dm_indicator_falls_back_to_reaction_when_set_status_unsupported(
    monkeypatch, error_code
):
    """A DM that is not an assistant thread must still show *something*.

    ``assistant.threads.setStatus`` only works inside a real assistant thread.
    When it refuses, the reaction is the whole indicator — previously this
    branch returned early and the DM showed no sign the agent was working.
    """
    reactions: list[dict] = []

    async def fake_set_status(self, **kwargs):
        raise _api_error(error_code)

    async def fake_reactions_add(self, **kwargs):
        reactions.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(AsyncWebClient, "assistant_threads_setStatus", fake_set_status)
    monkeypatch.setattr(AsyncWebClient, "reactions_add", fake_reactions_add)

    svc = SlackPlatformService(
        credentials={"access_token": "xoxb-test", "scope": "chat:write,assistant:write"}
    )
    await svc.add_processing_indicator(event=_dm_event())

    assert reactions == [{"channel": "D1", "name": "eyes", "timestamp": "100.0"}]


async def test_dm_indicator_propagates_unexpected_set_status_errors(monkeypatch):
    """An error we do not recognise is a real failure, not a fallback signal."""

    async def fake_set_status(self, **kwargs):
        raise _api_error("account_inactive")

    async def fake_reactions_add(self, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("should not fall back on an unexpected error")

    monkeypatch.setattr(AsyncWebClient, "assistant_threads_setStatus", fake_set_status)
    monkeypatch.setattr(AsyncWebClient, "reactions_add", fake_reactions_add)

    svc = SlackPlatformService(
        credentials={"access_token": "xoxb-test", "scope": "assistant:write"}
    )
    with pytest.raises(SlackApiError):
        await svc.add_processing_indicator(event=_dm_event())


async def test_list_channels_retries_public_only_when_private_scope_missing(
    monkeypatch,
):
    """A workspace installed before ``groups:read`` still gets a usable picker."""
    requested_types: list[str] = []

    async def fake_conversations_list(self, **kwargs):
        requested_types.append(kwargs["types"])
        if "private_channel" in kwargs["types"]:
            raise _api_error("missing_scope")
        return {
            "channels": [{"id": "C1", "name": "general", "is_member": True}],
            "response_metadata": {"next_cursor": ""},
        }

    monkeypatch.setattr(AsyncWebClient, "conversations_list", fake_conversations_list)

    svc = SlackPlatformService(credentials={"access_token": "xoxb-test"})
    channels = await svc.list_channels()

    assert requested_types == ["public_channel,private_channel", "public_channel"]
    assert [channel.id for channel in channels] == ["C1"]


async def test_list_channels_propagates_other_errors(monkeypatch):
    async def fake_conversations_list(self, **kwargs):
        raise _api_error("invalid_auth")

    monkeypatch.setattr(AsyncWebClient, "conversations_list", fake_conversations_list)

    svc = SlackPlatformService(credentials={"access_token": "xoxb-test"})
    with pytest.raises(SlackApiError):
        await svc.list_channels()
