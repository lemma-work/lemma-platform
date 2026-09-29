"""Every way a private conversation can fail to open is the same fact.

`events.handlers._context_for_delivery` catches `PrivateDeliveryUnavailable`
and falls through to ordinary ingestion, which is what answers a stranger. The
shape checks in this module raised that; the two provider calls raised
`SlackApiError` and `httpx.HTTPStatusError` instead, which went past that
handler, out of the subscriber, and left the person with nothing at all -- on
every message, for as long as the installation stayed that way.

So these ask one question twice: does the refusal that comes back from the
provider arrive as the type the one caller that matters is looking for.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from slack_sdk.errors import SlackApiError

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.onboarding_private_delivery import (
    PrivateDeliveryUnavailable,
    private_onboarding_destination,
)

pytestmark = pytest.mark.unit


def _channel_event(platform: SurfacePlatform, **overrides) -> ParsedInboundSurfaceEvent:
    payload = {
        "platform": platform,
        "external_message_id": "m1",
        "external_channel_id": "Croom",
        "external_thread_id": "Croom",
        "message_text": "help with my forecast",
        "sender_external_user_id": "U123",
        "tenant_id": "T999",
        "is_dm": False,
        "conversation_type": ConversationType.EXTERNAL_GROUP,
        "reply_target": {"channel": "Croom"},
    }
    payload.update(overrides)
    return ParsedInboundSurfaceEvent(**payload)


async def test_a_slack_workspace_without_im_write_is_a_delivery_refusal() -> None:
    """The exact installation this was found on: an app missing `im:write`.

    `conversations.open` answers `missing_scope`, and the slack SDK turns that
    into `SlackApiError`. Nothing between here and the subscriber caught it.
    """
    refused = SlackApiError(
        "missing_scope", SimpleNamespace(data={"ok": False, "error": "missing_scope"})
    )
    client = SimpleNamespace(conversations_open=AsyncMock(side_effect=refused))

    with pytest.raises(PrivateDeliveryUnavailable):
        await private_onboarding_destination(
            _channel_event(SurfacePlatform.SLACK),
            credentials={"access_token": "xoxb"},
            slack_client=AsyncMock(return_value=client),
        )


async def test_a_teams_bot_that_cannot_create_the_conversation_is_the_same_refusal() -> (
    None
):
    """Teams says no with a status code, which `raise_for_status` re-throws.

    Only the answered-and-refused case converts. A timeout or a dropped
    connection stays what it is, because the Bot Framework may well be there on
    the next attempt and a retry is the right answer to that.
    """
    request = httpx.Request("POST", "https://smba.example/v3/conversations")
    response = httpx.Response(403, request=request, json={"error": "Forbidden"})
    event = _channel_event(
        SurfacePlatform.TEAMS,
        reply_target={"service_url": "https://smba.example"},
        raw_payload={"from": {"id": "29:sender"}, "recipient": {"id": "28:bot"}},
    )
    with pytest.raises(PrivateDeliveryUnavailable):
        await private_onboarding_destination(
            event,
            credentials={},
            http_client=lambda: SimpleNamespace(post=AsyncMock(return_value=response)),
            bot_token=AsyncMock(return_value="bot-token"),
        )


async def test_a_transport_failure_is_still_a_transport_failure() -> None:
    """The half that must not convert, pinned so the conversion cannot widen.

    Turning a connect error into `PrivateDeliveryUnavailable` would send a
    person who could have been taken aside a second later down the "please sign
    up" path instead, permanently, on one bad moment.
    """
    event = _channel_event(
        SurfacePlatform.TEAMS,
        reply_target={"service_url": "https://smba.example"},
        raw_payload={"from": {"id": "29:sender"}, "recipient": {"id": "28:bot"}},
    )
    with pytest.raises(httpx.ConnectError):
        await private_onboarding_destination(
            event,
            credentials={},
            http_client=lambda: SimpleNamespace(
                post=AsyncMock(side_effect=httpx.ConnectError("no route"))
            ),
            bot_token=AsyncMock(return_value="bot-token"),
        )


async def test_a_stale_onboarding_click_is_handled_not_a_server_error() -> None:
    """The click is answered; it must not reach Slack as a 500 and be retried.

    A setup button whose payload names no token cannot be honoured, and
    `open_onboarding_modal` says so with `PrivateDeliveryUnavailable` -- a
    `RuntimeError`, so uncaught it left the webhook route as a 500, which Slack
    reads as a failed delivery and retries into the same refusal. An expired or
    foreign token takes the same road through `require_input`.
    """
    from app.modules.agent_surfaces.services.onboarding_slack_modal import (
        OPEN_ACTION,
        open_onboarding_modal,
    )

    payload = {
        "type": "block_actions",
        "actions": [{"action_id": OPEN_ACTION}],
        "user": {"id": "U1"},
        "team": {"id": "T1"},
    }

    assert await open_onboarding_modal(payload, None, uows=AsyncMock()) is True
