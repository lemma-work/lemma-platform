"""A Slack message, as the connector triggers the catalog offers for it.

The Slack connector advertises three triggers -- a message in a channel, a
reply in one thread, the app mentioned -- and their events arrive where every
Slack event does: the pod's bot's endpoint. This turns one heard message into
the routing keys those triggers' schedules carry (`schedule`'s
`_slack_binding` writes the other half), so the two can be held to agreeing.

Pure: what Slack said, nothing looked up. Which pods the message is for is the
caller's to say (`services/channel_triggers`).
"""

from __future__ import annotations

import hashlib

from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.schedule.contracts.webhook_source import (
    NormalizedWebhook,
    WebhookPayload,
)

SLACK_TRIGGER_SOURCE = "slack"
#: The catalog's `event_type` of each trigger.
CHANNEL_MESSAGE = "message"
THREAD_REPLY = "message.thread"
APP_MENTION = "app_mention"

#: Slack's legacy verification token rides on every event body. It is the
#: app's, not the message's, and a run has no use for it.
_WITHHELD_FROM_RUNS = frozenset({"token"})


def slack_trigger_events(
    parsed: ParsedInboundSurfaceEvent, *, pod_ids: frozenset[str]
) -> list[NormalizedWebhook]:
    """The trigger events one heard Slack message is, for these pods.

    A `message` is a channel message, and a thread reply too when it answers a
    thread; an `app_mention` is only a mention. Slack sends both for a message
    that mentions the app, each with its own event id, and each fires only the
    triggers of its own kind: a schedule on either fires once for the message,
    never twice.

    A channel message includes replies in its threads: Slack's own
    `message.channels` does, and following one channel as it happens -- an
    incident's, say -- means following its threads.
    """
    if parsed.platform is not SurfacePlatform.SLACK or parsed.is_dm:
        return []
    team_id = (parsed.tenant_id or "").strip()
    channel_id = (parsed.external_channel_id or "").strip()
    ts = (parsed.external_message_id or "").strip()
    if not (team_id and channel_id and ts and pod_ids):
        return []
    kind = parsed.metadata.get("event_type")
    if kind == "app_mention":
        names = [APP_MENTION]
    elif kind == "message":
        names = [CHANNEL_MESSAGE]
        if parsed.metadata.get("is_thread_reply"):
            names.append(THREAD_REPLY)
    else:
        return []
    payload = {
        key: value
        for key, value in parsed.raw_payload.items()
        if key not in _WITHHELD_FROM_RUNS
    }
    return [
        NormalizedWebhook(
            payload=payload,
            source_event_id=slack_source_event_id(name, team_id, channel_id, ts),
            match=_routing_key(name, team_id, channel_id, parsed.external_thread_id),
            pod_ids=pod_ids,
        )
        for name in names
    ]


def slack_source_event_id(event: str, team_id: str, channel_id: str, ts: str) -> str:
    """A stable id for the *message*, not for the delivery.

    Not Slack's `event_id`, though a retry repeats it: a channel can hold
    several of a pod's bots, each its own app, and each app is sent its own
    event -- with its own id -- for the same message. Keyed on the message
    (a `ts` is unique in its channel), a schedule fires once whichever bots
    heard it and however often Slack retried.
    """
    material = f"slack:{event}:{team_id}:{channel_id}:{ts}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _routing_key(
    event: str, team_id: str, channel_id: str, thread_ts: str
) -> WebhookPayload:
    key: WebhookPayload = {
        "source": SLACK_TRIGGER_SOURCE,
        "event": event,
        "team_id": team_id,
        "channel_id": channel_id,
    }
    if event == THREAD_REPLY:
        key["thread_ts"] = thread_ts
    return key


__all__ = [
    "APP_MENTION",
    "CHANNEL_MESSAGE",
    "SLACK_TRIGGER_SOURCE",
    "THREAD_REPLY",
    "slack_source_event_id",
    "slack_trigger_events",
]
