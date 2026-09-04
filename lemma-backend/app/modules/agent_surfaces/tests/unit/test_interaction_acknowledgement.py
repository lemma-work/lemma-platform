"""Telling a person what happened when they tapped a button.

Only Telegram used to. Everywhere else the tap produced no confirmation, left
the control live, and reported a failure to nobody — so these assert the wire
payload each platform now sends, not merely that a method exists.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.modules.agent_surfaces.domain.entities import ParsedSurfaceInteraction
from app.modules.agent_surfaces.platforms.slack.message_blocks import (
    slack_acknowledgement_body,
)


# --- Slack: the response_url body ----------------------------------------

_CARD = {
    "text": "Approval needed: Delete order 42",
    "blocks": [
        {"type": "section", "text": {"type": "mrkdwn", "text": "Delete order 42"}},
        {"type": "actions", "elements": [{"type": "button", "text": "Approve"}]},
    ],
}


def test_a_settled_decision_rewrites_the_card_without_its_buttons() -> None:
    body = slack_acknowledgement_body(_CARD, text="Approved", clear_actions=True)
    assert body["replace_original"] is True
    types = [block["type"] for block in body["blocks"]]
    assert "actions" not in types, "the button stayed tappable after the decision"
    assert types[0] == "section", "the card itself should survive, minus its actions"
    assert body["blocks"][-1]["elements"][0]["text"] == "Approved"


def test_a_settled_decision_always_carries_a_text_fallback() -> None:
    """Slack needs one whenever blocks are sent; it is the notification preview."""
    assert slack_acknowledgement_body(_CARD, text="", clear_actions=True)["text"]


def test_an_unsettled_note_is_ephemeral_and_leaves_the_card_alone() -> None:
    """ "Reply with your own answer" — nobody else in the channel is waiting."""
    body = slack_acknowledgement_body(
        _CARD, text="Reply with your own answer.", clear_actions=False
    )
    assert body["response_type"] == "ephemeral"
    assert body["replace_original"] is False
    assert body["text"] == "Reply with your own answer."


async def _slack_ack(payload: dict[str, Any], **kwargs: Any) -> list[Any]:
    from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService

    posted: list[Any] = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, url, json):  # noqa: A002 - httpx keyword
            posted.append((url, json))

    service = SlackPlatformService(credentials={"access_token": "xoxb-test"})
    with patch(
        "app.modules.agent_surfaces.platforms.slack.service.httpx.AsyncClient",
        lambda **_: _Client(),
    ):
        await service.acknowledge_interaction(
            ParsedSurfaceInteraction(platform="SLACK", raw_payload=payload),
            text="Done",
            show_alert=False,
            clear_actions=True,
            **kwargs,
        )
    return posted


async def test_slack_posts_the_acknowledgement_to_the_response_url() -> None:
    posted = await _slack_ack({"response_url": "https://hooks.slack/r/1", **_CARD})
    assert len(posted) == 1
    url, body = posted[0]
    assert url == "https://hooks.slack/r/1"
    assert body["replace_original"] is True


async def test_slack_without_a_response_url_sends_nothing_rather_than_raising() -> None:
    """The decision is already recorded; a failed acknowledgement must not undo it."""
    assert await _slack_ack({"message": _CARD}) == []


# --- Teams: editing the activity the card was in --------------------------


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Session:
    def __init__(self, put_status: int = 200) -> None:
        self.put_status = put_status
        self.calls: list[tuple[str, str]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def put(self, url, headers=None, json=None):
        self.calls.append(("PUT", url))
        return _Response(self.put_status)

    def post(self, url, headers=None, json=None):
        self.calls.append(("POST", url))
        return _Response(200)


def _teams_interaction(*, reply_to_id: str | None) -> ParsedSurfaceInteraction:
    return ParsedSurfaceInteraction(
        platform="TEAMS",
        tenant_id="tenant-1",
        reply_target={
            "service_url": "https://smba.trafficmanager.net/emea",
            "conversation_id": "19:conv",
            "reply_to_id": reply_to_id,
        },
    )


async def _teams_ack(session: _Session, interaction, **kwargs: Any) -> None:
    from app.modules.agent_surfaces.platforms.teams.adapter import TeamsSurfaceAdapter

    adapter = TeamsSurfaceAdapter()
    with (
        patch.object(
            TeamsSurfaceAdapter, "_get_bot_token", new=AsyncMock(return_value="token")
        ),
        patch(
            "app.modules.agent_surfaces.platforms.teams.adapter_egress"
            ".new_aiohttp_session",
            lambda *a, **k: session,
        ),
    ):
        await adapter.acknowledge_interaction(
            credentials={}, interaction=interaction, text="Approved", **kwargs
        )


async def test_teams_edits_the_card_activity_so_the_buttons_retire() -> None:
    """An Adaptive Card's actions stay tappable forever unless the activity is replaced."""
    session = _Session()
    await _teams_ack(
        session, _teams_interaction(reply_to_id="act-7"), clear_actions=True
    )
    assert session.calls == [
        (
            "PUT",
            "https://smba.trafficmanager.net/emea/v3/conversations/19%3Aconv/activities/act-7",
        )
    ]


async def test_teams_posts_a_new_message_when_the_card_cannot_be_edited() -> None:
    session = _Session(put_status=403)
    await _teams_ack(
        session, _teams_interaction(reply_to_id="act-7"), clear_actions=True
    )
    assert [method for method, _ in session.calls] == ["PUT", "POST"]


async def test_teams_posts_rather_than_edits_when_the_decision_is_still_open() -> None:
    session = _Session()
    await _teams_ack(
        session, _teams_interaction(reply_to_id="act-7"), clear_actions=False
    )
    assert [method for method, _ in session.calls] == ["POST"]


# --- WhatsApp: a new message, and only when it says something -------------


async def _whatsapp_ack(*, text: str, show_alert: bool) -> list[dict[str, Any]]:
    from app.modules.agent_surfaces.platforms.whatsapp.service import (
        WhatsAppPlatformService,
    )

    service = WhatsAppPlatformService(
        {"access_token": "token", "phone_number_id": "pn-1"}
    )
    sent: list[dict[str, Any]] = []
    service._client.send_message_payload = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda **kwargs: sent.append(kwargs["payload"])
    )
    await service.acknowledge_interaction(
        ParsedSurfaceInteraction(
            platform="WHATSAPP", reply_target={"sender_wa_id": "4477"}
        ),
        text=text,
        show_alert=show_alert,
        clear_actions=True,
    )
    return sent


async def test_whatsapp_says_the_outcomes_a_tap_cannot_imply() -> None:
    sent = await _whatsapp_ack(text="This action expired.", show_alert=True)
    assert sent == [
        {
            "messaging_product": "whatsapp",
            "to": "4477",
            "type": "text",
            "text": {"body": "This action expired."},
        }
    ]


@pytest.mark.parametrize("text", ["Done", "Retrying…"])
async def test_whatsapp_stays_quiet_on_a_routine_confirmation(text: str) -> None:
    """No edit API here, so a confirmation costs a message that repeats their own tap."""
    assert await _whatsapp_ack(text=text, show_alert=False) == []
