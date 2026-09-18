"""What Slack POSTs to `/surfaces/webhooks/slack`.

Every shape here starts from `fixtures/slack_dm_event.json`, which is a real
`event_callback` envelope, and edits the one part that makes it a different
case. Restating the envelope per case is how the copies in four test files
drifted apart in the first place.

Slack sends every event to one URL, so the parser's first job is to refuse most
of what arrives — a bot's own message, a message subtype, a channel message
with no mention. Those refusals are cases too: they are the difference between
an agent that answers when spoken to and one that answers its own replies.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    SurfacePlatform,
)
from app.modules.agent_surfaces.tests.e2e.platform_payloads.registry import (
    InboundCase,
    InteractionCase,
    register_inbound,
    register_interactions,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "slack_dm_event.json"

TEAM_ID = "T0123456"
APP_ID = "A0123456"
BOT_USER_ID = "U0AGSSTQZLH"
SENDER_ID = "U0123456"
DM_CHANNEL_ID = "D0123456"
CHANNEL_ID = "C0123456"

#: Slack's own name for the approve/deny controls, read from the rendered
#: message rather than restated — see `control_value`.
APPROVE_ACTION_ID = "lemma_approval_approve"
DENY_ACTION_ID = "lemma_approval_deny"
#: The submit button a native `ask_user` form renders.
FORM_SUBMIT_ACTION_ID = "lemma_form_submit"


def envelope(
    *,
    text: str = "Hello from Slack DM",
    ts: str = "1700000000.000100",
    channel: str = DM_CHANNEL_ID,
    channel_type: str = "im",
    event_type: str = "message",
    sender: str = SENDER_ID,
    event_id: str | None = None,
    thread_ts: str | None = None,
    extra_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The committed capture, with the fields a case varies substituted."""
    payload = json.loads(FIXTURE.read_text())
    payload["event_id"] = event_id or f"Ev{ts.replace('.', '')}"
    event = payload["event"]
    event.update(
        {
            "type": event_type,
            "user": sender,
            "text": text,
            "channel": channel,
            "channel_type": channel_type,
            "ts": ts,
            "event_ts": ts,
        }
    )
    if thread_ts:
        event["thread_ts"] = thread_ts
    if channel_type != "im":
        event.pop("assistant_thread", None)
    if extra_event:
        event.update(extra_event)
    return payload


def dm(
    *,
    text: str = "Hello from Slack DM",
    ts: str = "1700000000.000100",
    thread_ts: str | None = None,
) -> dict:
    """A direct message. ``thread_ts`` makes it a reply in an existing thread,
    which is what continues a conversation rather than starting one."""
    return envelope(text=text, ts=ts, thread_ts=thread_ts)


def channel_mention(
    *, text: str = "what is on my plate?", ts: str = "1700000000.000200"
) -> dict:
    return envelope(
        text=f"<@{BOT_USER_ID}> {text}",
        ts=ts,
        channel=CHANNEL_ID,
        channel_type="channel",
        event_type="app_mention",
    )


def dm_with_file(
    *,
    text: str = "please read this",
    ts: str = "1700000000.000300",
    file_id: str = "F0123456",
    name: str = "invoice.txt",
    mimetype: str = "text/plain",
) -> dict:
    return envelope(
        text=text,
        ts=ts,
        extra_event={
            "files": [
                {
                    "id": file_id,
                    "name": name,
                    "title": name,
                    "mimetype": mimetype,
                    "filetype": "text",
                    "size": 42,
                    "url_private_download": f"https://files.slack.test/{file_id}",
                }
            ]
        },
    )


def bot_message(*, ts: str = "1700000000.000400") -> dict:
    """The app's own reply coming back on the same webhook."""
    payload = envelope(text="the agent's own answer", ts=ts)
    payload["event"]["bot_id"] = "B0123456"
    payload["event"]["app_id"] = APP_ID
    payload["event"].pop("user", None)
    return payload


def button_press(
    *,
    value: str,
    action_id: str,
    channel: str = DM_CHANNEL_ID,
    sender: str = SENDER_ID,
    message_ts: str = "1700000000.700700",
    action_ts: str = "1700000000.700800",
) -> dict[str, Any]:
    """A `block_actions` submission tapping one rendered button.

    `value` is the callback token the agent put on the button, and `action_id`
    is what it called the control; both are read back out of the message the
    fake Slack received rather than written again here.
    """
    return {
        "type": "block_actions",
        "api_app_id": APP_ID,
        "user": {"id": sender},
        "team": {"id": TEAM_ID},
        "channel": {"id": channel},
        "container": {"message_ts": message_ts},
        "message": {"ts": message_ts},
        "actions": [
            {"action_id": action_id, "value": value, "action_ts": action_ts},
        ],
    }


def form_submit(
    *,
    value: str,
    fields: dict[str, str],
    action_id: str = FORM_SUBMIT_ACTION_ID,
    channel: str = DM_CHANNEL_ID,
    sender: str = SENDER_ID,
    message_ts: str = "1700000000.700900",
) -> dict[str, Any]:
    """A `block_actions` submit carrying the answers a native form collected.

    The native render keys each select by the question's header (which is the
    `block_id`) and uses the option label as its value, so an answer flattens
    to `{header: label}`. `static_select` rather than a text input because that
    is what `ask_user` renders.
    """
    payload = button_press(
        value=value,
        action_id=action_id,
        channel=channel,
        sender=sender,
        message_ts=message_ts,
    )
    payload["state"] = {
        "values": {
            header: {
                header: {
                    "type": "static_select",
                    "selected_option": {"value": answer},
                }
            }
            for header, answer in fields.items()
        }
    }
    return payload


@register_inbound(SurfacePlatform.SLACK)
def inbound_cases() -> list[InboundCase]:
    return [
        InboundCase(
            name="dm",
            platform=SurfacePlatform.SLACK,
            payload=dm(),
            expected={
                "conversation_type": ConversationType.EXTERNAL_DM,
                "tenant_id": TEAM_ID,
                "external_channel_id": DM_CHANNEL_ID,
                "external_message_id": "1700000000.000100",
                "sender_external_user_id": SENDER_ID,
                "message_text": "Hello from Slack DM",
                "is_dm": True,
            },
        ),
        InboundCase(
            name="channel_mention",
            platform=SurfacePlatform.SLACK,
            payload=channel_mention(),
            expected={
                "conversation_type": ConversationType.EXTERNAL_GROUP,
                "external_channel_id": CHANNEL_ID,
                "sender_external_user_id": SENDER_ID,
                "is_dm": False,
                "mentioned_agent": True,
            },
        ),
        InboundCase(
            name="dm_with_file",
            platform=SurfacePlatform.SLACK,
            payload=dm_with_file(),
            expected={
                "is_dm": True,
                "sender_external_user_id": SENDER_ID,
            },
        ),
        InboundCase(
            name="bot_message_is_refused",
            platform=SurfacePlatform.SLACK,
            payload=bot_message(),
            expected={},
            refused=True,
        ),
    ]


@register_interactions(SurfacePlatform.SLACK)
def interaction_cases() -> list[InteractionCase]:
    callback = "11111111-1111-4111-8111-111111111111|call_abc"
    return [
        InteractionCase(
            name="approval_button",
            platform=SurfacePlatform.SLACK,
            payload=button_press(value=callback, action_id=APPROVE_ACTION_ID),
            expected={
                "callback_id": callback,
                "approval_decision": "APPROVE_ONCE",
                "external_user_id": SENDER_ID,
                "external_channel_id": DM_CHANNEL_ID,
                "tenant_id": TEAM_ID,
                "values": {},
            },
        ),
        InteractionCase(
            name="deny_button",
            platform=SurfacePlatform.SLACK,
            payload=button_press(value=callback, action_id=DENY_ACTION_ID),
            expected={"callback_id": callback, "approval_decision": "DENY"},
        ),
        InteractionCase(
            name="unknown_action_is_refused",
            platform=SurfacePlatform.SLACK,
            payload=button_press(value=callback, action_id="something_else"),
            expected={},
            refused=True,
        ),
    ]


def copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(payload)
