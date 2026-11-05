"""Telling a person what happened when they tapped a button.

Only Telegram used to. Everywhere else the tap produced no confirmation, left
the control live, and reported a failure to nobody — so these assert the wire
payload each platform now sends, not merely that a method exists.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.modules.agent_surfaces.domain.entities import ParsedSurfaceInteraction
from app.modules.agent_surfaces.tests.fakes import (
    PlatformHandler,
    PlatformHttp,
    answer_json,
    platform_http,
)
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


def _bot_framework(*, put_status: int = 200) -> PlatformHandler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(put_status, json={})
        return httpx.Response(200, json={"id": "new-activity"})

    return handler


async def _slack_ack(payload: dict[str, Any], **kwargs: Any) -> list[httpx.Request]:
    from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService

    service = SlackPlatformService(credentials={"access_token": "xoxb-test"})
    with platform_http(answer_json({"ok": True})) as http:
        await service.acknowledge_interaction(
            ParsedSurfaceInteraction(platform="SLACK", raw_payload=payload),
            text="Done",
            show_alert=False,
            clear_actions=True,
            **kwargs,
        )
    return http.requests


async def test_slack_posts_the_acknowledgement_to_the_response_url() -> None:
    posted = await _slack_ack({"response_url": "https://hooks.slack/r/1", **_CARD})
    assert len(posted) == 1
    assert str(posted[0].url) == "https://hooks.slack/r/1"
    assert PlatformHttp.json_of(posted[0])["replace_original"] is True


async def test_slack_without_a_response_url_sends_nothing_rather_than_raising() -> None:
    """The decision is already recorded; a failed acknowledgement must not undo it."""
    assert await _slack_ack({"message": _CARD}) == []


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


async def _teams_ack(
    interaction: ParsedSurfaceInteraction, *, put_status: int = 200, **kwargs: Any
) -> list[tuple[str, str]]:
    from app.modules.agent_surfaces.platforms.teams.adapter import TeamsSurfaceAdapter

    adapter = TeamsSurfaceAdapter()
    with (
        patch.object(
            TeamsSurfaceAdapter, "_get_bot_token", new=AsyncMock(return_value="token")
        ),
        platform_http(_bot_framework(put_status=put_status)) as http,
    ):
        await adapter.acknowledge_interaction(
            credentials={}, interaction=interaction, text="Approved", **kwargs
        )
    return [(request.method, str(request.url)) for request in http.requests]


async def test_teams_edits_the_card_activity_so_the_buttons_retire() -> None:
    """An Adaptive Card's actions stay tappable forever unless the activity is replaced."""
    calls = await _teams_ack(
        _teams_interaction(reply_to_id="act-7"), clear_actions=True
    )
    assert calls == [
        (
            "PUT",
            "https://smba.trafficmanager.net/emea/v3/conversations/19%3Aconv/activities/act-7",
        )
    ]


async def test_teams_posts_a_new_message_when_the_card_cannot_be_edited() -> None:
    calls = await _teams_ack(
        _teams_interaction(reply_to_id="act-7"), put_status=403, clear_actions=True
    )
    assert [method for method, _ in calls] == ["PUT", "POST"]


async def test_teams_posts_rather_than_edits_when_the_decision_is_still_open() -> None:
    calls = await _teams_ack(
        _teams_interaction(reply_to_id="act-7"), clear_actions=False
    )
    assert [method for method, _ in calls] == ["POST"]


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


async def test_whatsapp_answers_from_the_number_the_tap_arrived_on() -> None:
    """A pool means the adapter's configured number is not necessarily theirs.

    Everything else WhatsApp sends -- replies, progress, files -- prefers
    `reply_target["phone_number_id"]`, the number the message came in on. The
    interaction path had neither half: the parser never put it in the reply
    target, so the acknowledgement had nothing to prefer and fell back to the
    number this adapter happened to be built with.

    With one number those were the same value. With a pool they are not, and a
    person who tapped a button in their chat with number B was answered by
    number A -- a stranger, in a thread they were not looking at, about
    something they had just done somewhere else.

    Driven through the parser rather than a hand-built reply target, because the
    two halves are only worth anything together.
    """
    from app.modules.agent_surfaces.platforms.whatsapp.parser import (
        WhatsAppMessageParser,
    )
    from app.modules.agent_surfaces.platforms.whatsapp.payloads import (
        WHATSAPP_INTERACTION_SEP,
    )
    from app.modules.agent_surfaces.platforms.whatsapp.service import (
        WhatsAppPlatformService,
    )

    interaction = WhatsAppMessageParser().parse_interaction(
        {
            "entry": [
                {
                    "id": "waba-1",
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "arrived-on-b"},
                                "messages": [
                                    {
                                        "id": "wamid.1",
                                        "from": "4477",
                                        "type": "interactive",
                                        "interactive": {
                                            "button_reply": {
                                                "id": WHATSAPP_INTERACTION_SEP.join(
                                                    ("cb-1", "confirm", "yes")
                                                ),
                                                "title": "Yes",
                                            }
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                }
            ]
        }
    )

    assert interaction is not None
    assert interaction.reply_target["phone_number_id"] == "arrived-on-b"

    service = WhatsAppPlatformService(
        {"access_token": "token", "phone_number_id": "configured-a"}
    )
    calls: list[dict[str, Any]] = []
    service._client.send_message_payload = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda **kwargs: calls.append(kwargs)
    )

    await service.acknowledge_interaction(
        interaction,
        text="This action expired.",
        show_alert=True,
        clear_actions=True,
    )

    assert [call["phone_number_id"] for call in calls] == ["arrived-on-b"], (
        "the acknowledgement went out from the deployment's number instead of "
        "the one the person is actually talking to"
    )
