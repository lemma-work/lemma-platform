"""Slack saying a message was edited or deleted.

Both arrive as a ``message`` event with a subtype: ``message_changed`` carries
the message as it now reads under ``message``, and ``message_deleted`` names
the gone one by ``deleted_ts``. A thread's root deleted while it has replies
is changed into a ``tombstone`` rather than deleted, and reads as deleted
here. What an app posted -- the bot's own answers among them -- is not a line
anybody heard, and is left alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.line_changes import (
    LineChangeKind,
    ParsedLineChange,
)
from app.modules.agent_surfaces.platforms.common import payload_section, payload_text
from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser

#: The ``message`` subtypes read here.
SLACK_LINE_CHANGE_SUBTYPES = frozenset({"message_changed", "message_deleted"})


def _envelope(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("payload", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict) and nested.get("type") == "event_callback":
            return nested
    return payload


def _from_an_app(message: Mapping[str, Any]) -> bool:
    return bool(payload_text(message, "bot_id")) or (
        payload_text(message, "subtype") == "bot_message"
    )


def slack_line_changes(
    payload: Mapping[str, Any], parser: SlackMessageParser
) -> list[ParsedLineChange]:
    """The edit or deletion this event callback reports, if it reports one."""
    envelope = _envelope(payload)
    event = payload_section(envelope, "event")
    subtype = payload_text(event, "subtype")
    if (
        envelope.get("type") != "event_callback"
        or payload_text(event, "type") != "message"
        or subtype not in SLACK_LINE_CHANGE_SUBTYPES
    ):
        return []
    change = _deletion(envelope, event)
    if change is None:
        return []
    message = payload_section(event, "message")
    if subtype == "message_deleted" or payload_text(message, "subtype") == "tombstone":
        return [change]
    edited = _as_it_reads_now(envelope, event, parser)
    if edited is None:
        return []
    return [change.model_copy(update={"kind": LineChangeKind.EDITED, "edited": edited})]


def _deletion(
    envelope: Mapping[str, Any], event: Mapping[str, Any]
) -> ParsedLineChange | None:
    """The change, read as a deletion: which message, where, whose."""
    message = payload_section(event, "message")
    before = payload_section(event, "previous_message")
    if _from_an_app(message) or _from_an_app(before):
        return None
    channel = payload_text(event, "channel").strip()
    ts = (
        payload_text(event, "deleted_ts")
        or payload_text(message, "ts")
        or payload_text(before, "ts")
    ).strip()
    if not channel or not ts:
        return None
    sender = (payload_text(message, "user") or payload_text(before, "user")).strip()
    return ParsedLineChange(
        platform="SLACK",
        kind=LineChangeKind.DELETED,
        external_message_id=ts,
        external_channel_id=channel,
        is_dm=payload_text(event, "channel_type") == "im" or channel.startswith("D"),
        sender_external_ids=(sender,) if sender else (),
        tenant_id=payload_text(envelope, "team_id").strip() or None,
    )


def _as_it_reads_now(
    envelope: Mapping[str, Any], event: Mapping[str, Any], parser: SlackMessageParser
) -> ParsedInboundSurfaceEvent | None:
    """The changed message, parsed as Slack's message event of it would be."""
    message = payload_section(event, "message")
    now = {key: value for key, value in message.items() if key != "subtype"}
    now.update({"type": "message", "channel": payload_text(event, "channel").strip()})
    if "channel_type" in event:
        now["channel_type"] = event["channel_type"]
    return parser.parse({**envelope, "event": now})


__all__ = ["SLACK_LINE_CHANGE_SUBTYPES", "slack_line_changes"]
