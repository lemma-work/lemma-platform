"""What the Bot Framework POSTs to `/surfaces/webhooks/teams`.

`fixtures/teams_channel_mention_event.json` is a real capture — real AAD object
ids, a real `19:...@thread.tacv2` conversation id, a real mention entity — and
every case here starts from it. Teams is the platform where a hand-written
payload is most likely to be subtly wrong: the mention lives in `entities`, the
tenant in two places, and the thread id in `channelData` as well as in
`conversation.id`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.teams.parser import (
    TEAMS_APPROVAL_DECISION_KEY,
    TEAMS_FORM_CALLBACK_KEY,
)
from app.modules.agent_surfaces.tests.e2e.platform_payloads.registry import (
    InboundCase,
    InteractionCase,
    register_inbound,
    register_interactions,
)

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "teams_channel_mention_event.json"
)

TENANT_ID = "1b5c589f-1718-42c8-8244-166fbe5dd8fc"
CHANNEL_ID = "19:3b0dc498aeeb42abba81a2f6dd46ec67@thread.tacv2"
THREAD_ID = "1776236638028"
BOT_ID = "28:c3e201f2-a8ef-4026-bef4-d3da0153b191"
SENDER_AAD_ID = "b20e77ef-bd6b-4636-9f5b-20dd28beba24"

#: The two keys a submitted Adaptive Card carries. Taken from the parser rather
#: than spelled again: a renderer and a parser that agree on a string, and a
#: test that keeps its own copy of it, is how a payload builder goes quietly
#: stale. The first version of this module wrote `lemma_callback_id`, and the
#: contract test said so on its first run.
FORM_CALLBACK_KEY = TEAMS_FORM_CALLBACK_KEY
APPROVAL_DECISION_KEY = TEAMS_APPROVAL_DECISION_KEY


def _capture(service_url: str) -> dict[str, Any]:
    payload = json.loads(FIXTURE.read_text())
    payload["serviceUrl"] = service_url
    return payload


def channel_mention(
    *,
    service_url: str,
    text: str = "Do you know shera?",
    activity_id: str = THREAD_ID,
    reply_to_id: str | None = None,
) -> dict[str, Any]:
    """A mention in the captured channel.

    ``reply_to_id`` is what makes it a reply *in* a thread rather than the
    start of one: the parser reads `replyToId or id` as the thread, so a
    follow-up without it opens a second conversation and the question asked in
    the first is never answered.
    """
    payload = _capture(service_url)
    payload["id"] = activity_id
    payload["text"] = f"<at>Lemma</at> {text}"
    if reply_to_id:
        payload["replyToId"] = reply_to_id
    return payload


def personal_dm(
    *,
    service_url: str,
    text: str = "Hello from Teams",
    activity_id: str = "teams-dm-activity-1",
    conversation_id: str = "a:1dm-conversation-id",
) -> dict[str, Any]:
    """The same capture, made personal: no team, no mention, `personal` type."""
    payload = _capture(service_url)
    payload["id"] = activity_id
    payload["text"] = text
    payload.pop("entities", None)
    payload.pop("attachments", None)
    payload["conversation"] = {
        "isGroup": False,
        "conversationType": "personal",
        "tenantId": TENANT_ID,
        "id": conversation_id,
    }
    payload["channelData"] = {"tenant": {"id": TENANT_ID}}
    return payload


def card_submit(
    *,
    service_url: str,
    values: dict[str, Any],
    activity_id: str = "teams-submit-activity-1",
    reply_to_id: str = THREAD_ID,
    conversation: dict[str, Any] | None = None,
    channel_data: dict[str, Any] | None = None,
    sender: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """An `Action.Submit` from a rendered Adaptive Card.

    Teams delivers a submit as an ordinary `message` activity whose `value` is
    the card's collected data and whose `text` is absent — which is why the
    ingress path has to try the interaction parser before the message parser.
    """
    capture = _capture(service_url)
    return {
        "type": "message",
        "id": activity_id,
        "serviceUrl": service_url,
        "from": sender or capture["from"],
        "conversation": conversation or capture["conversation"],
        "channelData": channel_data or capture["channelData"],
        "replyToId": reply_to_id,
        "value": values,
    }


def approval_submit(
    *, service_url: str, callback_id: str, decision: str
) -> dict[str, Any]:
    return card_submit(
        service_url=service_url,
        values={
            FORM_CALLBACK_KEY: callback_id,
            APPROVAL_DECISION_KEY: decision,
        },
    )


@register_inbound(SurfacePlatform.TEAMS)
def inbound_cases() -> list[InboundCase]:
    service_url = "https://smba.trafficmanager.test/amer/"
    return [
        InboundCase(
            name="channel_mention",
            platform=SurfacePlatform.TEAMS,
            payload=channel_mention(service_url=service_url),
            expected={
                "conversation_type": ConversationType.EXTERNAL_GROUP,
                "tenant_id": TENANT_ID,
                "external_channel_id": CHANNEL_ID,
                "sender_aad_object_id": SENDER_AAD_ID,
                "is_dm": False,
                "mentioned_agent": True,
            },
        ),
        InboundCase(
            name="personal_dm",
            platform=SurfacePlatform.TEAMS,
            payload=personal_dm(service_url=service_url),
            expected={
                "conversation_type": ConversationType.EXTERNAL_DM,
                "tenant_id": TENANT_ID,
                "sender_aad_object_id": SENDER_AAD_ID,
                "message_text": "Hello from Teams",
                "is_dm": True,
            },
        ),
    ]


@register_interactions(SurfacePlatform.TEAMS)
def interaction_cases() -> list[InteractionCase]:
    service_url = "https://smba.trafficmanager.test/amer/"
    callback = "11111111-1111-4111-8111-111111111111|call_abc"
    return [
        InteractionCase(
            name="approval_submit",
            platform=SurfacePlatform.TEAMS,
            payload=approval_submit(
                service_url=service_url, callback_id=callback, decision="APPROVE_ONCE"
            ),
            expected={
                "callback_id": callback,
                "approval_decision": "APPROVE_ONCE",
                "tenant_id": TENANT_ID,
            },
        ),
        InteractionCase(
            name="answer_submit",
            platform=SurfacePlatform.TEAMS,
            payload=card_submit(
                service_url=service_url,
                values={FORM_CALLBACK_KEY: callback, "choice": "blue"},
            ),
            expected={"callback_id": callback, "values": {"choice": "blue"}},
        ),
        InteractionCase(
            name="a_card_with_no_callback_is_refused",
            platform=SurfacePlatform.TEAMS,
            payload=card_submit(service_url=service_url, values={"choice": "blue"}),
            expected={},
            refused=True,
        ),
    ]
