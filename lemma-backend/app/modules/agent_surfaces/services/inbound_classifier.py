"""What an inbound delivery is, decided in the request from its body alone.

Prod received 71.5k Slack webhooks in a week against 2,277 agent runs from
every source: most of what a platform sends is not something anybody acts on
-- a bot's own message echoed back, a reaction, chatter in a channel the bot
was never asked into, a WhatsApp "delivered"/"read" receipt. Those are
answered ``200`` and dropped here, before a row or a work item exists for them.

What is left is one or more of:

* ``interaction`` -- a button, a modal, a callback. It has a provider deadline
  (Slack's ``trigger_id`` lives ~3 s), so it is handled in the request;
* ``platform_update`` -- something about the app or a group rather than a
  message (the bot joined a channel, the app home opened, a WhatsApp group was
  created, a send failed), or a heard message edited or deleted: a
  ``surface.platform_update`` item;
* ``message`` -- a person writing: a ``surface_inbound_messages`` row and a
  ``surface.ingest`` item, ordered per thread by :func:`thread_key`.

Conservative by construction: anything not recognised as droppable is a
message, and ingest decides. A wrong drop loses a message; a wrong keep costs
one item.

Pure: no I/O. The one database question ("is this Slack channel one a group
adopted?") is the caller's, and only asked when :attr:`Verdict.unless_adopted`
says the answer decides.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.platforms.slack.group_updates import (
    SLACK_GROUP_UPDATE_EVENTS,
)
from app.modules.agent_surfaces.platforms.slack.line_changes import (
    SLACK_LINE_CHANGE_SUBTYPES,
)
from app.modules.agent_surfaces.platforms.slack.uninstall import (
    UNINSTALL_EVENT_TYPES as SLACK_UNINSTALL_EVENTS,
)
from app.modules.agent_surfaces.platforms.teams.line_changes import (
    TEAMS_LINE_CHANGE_ACTIVITIES,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Verdict:
    drop_reason: str | None = None
    interaction: bool = False
    platform_update: bool = False
    message: bool = False
    #: A Slack channel message nobody addressed -- or an edit or deletion of
    #: one: dropped unless the channel is one a group adopted (where
    #: everything said is logged, and so changes with what was said).
    unless_adopted: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def dropped(self) -> bool:
        return self.drop_reason is not None

    @property
    def verdict(self) -> str:
        if self.drop_reason is not None:
            return "drop"
        parts = [
            name
            for name, on in (
                ("interaction", self.interaction),
                ("platform_update", self.platform_update),
                ("message", self.message),
            )
            if on
        ]
        return "+".join(parts) or "none"


def drop(reason: str) -> Verdict:
    return Verdict(drop_reason=reason)


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: object) -> list[dict[str, Any]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


# ------------------------------------------------------------------ Slack

_SLACK_INTERACTIONS = frozenset(
    {
        "block_actions",
        "view_submission",
        "view_closed",
        "shortcut",
        "message_action",
        "block_suggestion",
        "interactive_message",
    }
)
#: Message subtypes that are not a person writing (``SlackMessageParser``).
#: An edit or a deletion is not either, but changes a line that was heard
#: (``services/line_changes``).
_SLACK_IGNORED_SUBTYPES = frozenset(
    {
        "bot_message",
        "channel_join",
        "channel_leave",
        "message_replied",
        "channel_topic",
        "channel_purpose",
        "channel_name",
        "pinned_item",
        "unpinned_item",
        "ekm_access_denied",
    }
)
#: Events about the app rather than a message (``app_event_handler``).
_SLACK_LIFECYCLE = frozenset({"app_home_opened", "member_joined_channel"})
#: Everything for ``surface.platform_update``: the app's own situation, a
#: channel's (``group_updates``), and the app removed (``app_uninstall``).
_SLACK_PLATFORM_UPDATES = (
    _SLACK_LIFECYCLE | SLACK_GROUP_UPDATE_EVENTS | SLACK_UNINSTALL_EVENTS
)


def _slack_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("payload", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict) and nested.get("type"):
            return nested
    return dict(payload)


def classify_slack(payload: Mapping[str, Any]) -> Verdict:
    envelope = _slack_envelope(payload)
    kind = envelope.get("type")
    if kind in _SLACK_INTERACTIONS:
        return Verdict(interaction=True, message=True)
    if kind == "url_verification":
        return drop("handshake")
    if kind != "event_callback":
        return drop(f"slack_{kind or 'untyped'}")
    event = _dict(envelope.get("event"))
    event_type = event.get("type")
    if event_type in _SLACK_PLATFORM_UPDATES:
        return Verdict(platform_update=True)
    if event_type not in {"message", "app_mention"}:
        # reaction_added, user_change, channel_created, file_shared, ...
        return drop(f"slack_event_{event_type or 'untyped'}")
    return _classify_slack_message(event)


def _classify_slack_message(event: dict[str, Any]) -> Verdict:
    """A ``message`` or ``app_mention``: a person writing, or not."""
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return drop("bot_echo")
    if event.get("subtype") in SLACK_LINE_CHANGE_SUBTYPES:
        return _classify_slack_line_change(event)
    if event.get("subtype") in _SLACK_IGNORED_SUBTYPES:
        return drop(f"slack_{event.get('subtype')}")
    channel = str(event.get("channel") or "")
    if channel and not _slack_addressed(event, channel):
        return Verdict(message=True, unless_adopted=channel)
    return Verdict(message=True)


def _classify_slack_line_change(event: dict[str, Any]) -> Verdict:
    """An edit or deletion of a message: of a line heard in the channel, if
    the bot heard it. Not one of an app's own messages."""
    changed = _dict(event.get("message")) or _dict(event.get("previous_message"))
    if changed.get("bot_id") or changed.get("subtype") == "bot_message":
        return drop("bot_echo")
    channel = str(event.get("channel") or "")
    if channel and not channel.startswith("D"):
        return Verdict(platform_update=True, unless_adopted=channel)
    return Verdict(platform_update=True)


def _slack_addressed(event: dict[str, Any], channel: str) -> bool:
    """A DM, a mention, a reply in a thread, or a file: somebody asked."""
    thread_ts = event.get("thread_ts")
    return any(
        (
            event.get("channel_type") == "im" or channel.startswith("D"),
            event.get("type") == "app_mention",
            "<@" in str(event.get("text") or ""),
            thread_ts is not None and thread_ts != event.get("ts"),
            bool(event.get("files")),
        )
    )


# ------------------------------------------------------------------ Telegram


def classify_telegram(payload: Mapping[str, Any]) -> Verdict:
    if "callback_query" in payload:
        return Verdict(interaction=True)
    if "my_chat_member" in payload:
        return Verdict(platform_update=True)
    if "edited_message" in payload:
        # Not a new turn (``TelegramMessageParser``), but a heard line that
        # now says something else (``services/line_changes``).
        return Verdict(platform_update=True)
    if "edited_channel_post" in payload:
        return drop("edit")
    message = _dict(payload.get("message"))
    if not message:
        return drop("telegram_not_a_message")
    if message.get("left_chat_member") or message.get("new_chat_members"):
        # A service message: somebody left the group or joined it, nothing was
        # said.
        return Verdict(platform_update=True)
    if _dict(message.get("from")).get("is_bot"):
        return drop("bot_echo")
    return Verdict(message=True)


# ------------------------------------------------------------------ WhatsApp


def _whatsapp_values(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        _dict(change.get("value"))
        for entry in _dicts(payload.get("entry"))
        for change in _dicts(entry.get("changes"))
    ]


def _whatsapp_group_update(
    payload: Mapping[str, Any], values: list[dict[str, Any]]
) -> bool:
    if any(
        key in value
        for value in values
        for key in ("groups", "group_id", "group_status")
    ):
        return True
    return any(
        str(_dict(change).get("field") or "").startswith("group")
        for entry in _dicts(payload.get("entry"))
        for change in _dicts(entry.get("changes"))
    )


def classify_whatsapp(payload: Mapping[str, Any]) -> Verdict:
    values = _whatsapp_values(payload)
    messages = [
        message for value in values for message in _dicts(value.get("messages"))
    ]
    failed_statuses = any(
        status.get("status") == "failed"
        for value in values
        for status in _dicts(value.get("statuses"))
    )
    group_update = _whatsapp_group_update(payload, values)
    if not messages and not failed_statuses and not group_update:
        # Delivered and read receipts: they say nothing that needs doing.
        return drop("receipt" if values else "whatsapp_empty")
    return Verdict(
        interaction=any(m.get("type") in {"interactive", "button"} for m in messages),
        platform_update=failed_statuses or group_update,
        message=bool(messages),
    )


# ------------------------------------------------------------------ Teams


def classify_teams(payload: Mapping[str, Any]) -> Verdict:
    kind = payload.get("type")
    if kind == "invoke":
        return Verdict(interaction=True, message=True)
    if kind in {"conversationUpdate", "installationUpdate"}:
        return Verdict(platform_update=True)
    if kind in TEAMS_LINE_CHANGE_ACTIVITIES:
        # Not a new turn, but a heard line that now says something else, or
        # nothing (``services/line_changes``).
        if _dict(payload.get("from")).get("role") == "bot":
            return drop("bot_echo")
        return Verdict(platform_update=True)
    if kind in {"typing", "messageReaction", "event"}:
        return drop(f"teams_{kind}")
    if kind == "message":
        if _dict(payload.get("from")).get("role") == "bot":
            return drop("bot_echo")
        # A card's Action.Submit arrives as a message carrying ``value``.
        return Verdict(interaction="value" in payload, message=True)
    return Verdict(message=True)


def classify(
    platform: str | SurfacePlatform | None, payload: Mapping[str, Any]
) -> Verdict:
    """The verdict for one delivery on ``platform``. Unknown platforms ingest."""
    value = (
        platform.value if isinstance(platform, SurfacePlatform) else str(platform or "")
    ).upper()
    if value == SurfacePlatform.SLACK.value:
        return classify_slack(payload)
    if value == SurfacePlatform.TELEGRAM.value:
        return classify_telegram(payload)
    if value == SurfacePlatform.WHATSAPP.value:
        return classify_whatsapp(payload)
    if value == SurfacePlatform.TEAMS.value:
        return classify_teams(payload)
    return Verdict(message=True)


# ------------------------------------------------------------------ ordering


def _platform_value(platform: str | SurfacePlatform | None) -> str:
    return (
        platform.value if isinstance(platform, SurfacePlatform) else str(platform or "")
    ).upper()


def _slack_thread(payload: Mapping[str, Any]) -> str:
    envelope = _slack_envelope(payload)
    event = _dict(envelope.get("event"))
    channel = event.get("channel") or _dict(envelope.get("channel")).get("id")
    dm = str(channel or "").startswith("D")
    thread = event.get("thread_ts") or ("dm" if dm else event.get("ts"))
    return f"slack:{channel or '-'}:{thread or '-'}"


def _telegram_thread(payload: Mapping[str, Any]) -> str:
    message = _dict(payload.get("message")) or _dict(
        _dict(payload.get("callback_query")).get("message")
    )
    chat = _dict(message.get("chat")).get("id")
    return f"telegram:{chat or '-'}:{message.get('message_thread_id') or '-'}"


def _whatsapp_thread(payload: Mapping[str, Any]) -> str:
    for value in _whatsapp_values(payload):
        number = _dict(value.get("metadata")).get("phone_number_id") or "-"
        for message in _dicts(value.get("messages")):
            group = message.get("group_id") or _dict(message.get("context")).get(
                "group_id"
            )
            return f"whatsapp:{number}:{group or message.get('from') or '-'}"
    return "whatsapp:-"


def _teams_thread(payload: Mapping[str, Any]) -> str:
    return f"teams:{_dict(payload.get('conversation')).get('id') or '-'}"


def _resend_thread(payload: Mapping[str, Any]) -> str:
    sender = payload.get("from") or payload.get("sender")
    subject = str(payload.get("subject") or "").removeprefix("Re: ")
    return f"resend:{sender or '-'}:{subject[:64]}"


_THREADS = {
    SurfacePlatform.SLACK.value: _slack_thread,
    SurfacePlatform.TELEGRAM.value: _telegram_thread,
    SurfacePlatform.WHATSAPP.value: _whatsapp_thread,
    SurfacePlatform.TEAMS.value: _teams_thread,
    SurfacePlatform.RESEND.value: _resend_thread,
}


def thread_key(
    platform: str | SurfacePlatform | None, payload: Mapping[str, Any]
) -> str:
    """The conversation a delivery belongs to, for ordering two of them.

    ``surface.ingest`` runs one item per key at a time, so two webhooks for one
    thread are ingested in arrival order and the second joins (or follows) the
    run the first started. Best effort from the body: a key that is too coarse
    only serialises more than it must; one that is too fine lets two messages
    of one thread race, which is what ingest's own conversation lock is for.
    """
    value = _platform_value(platform)
    reader = _THREADS.get(value)
    return reader(payload) if reader is not None else f"{value.lower() or 'unknown'}:-"


__all__ = ["Verdict", "classify", "thread_key"]
