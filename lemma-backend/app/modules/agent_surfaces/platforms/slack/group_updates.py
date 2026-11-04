"""What Slack says about a channel itself: who came and went, and what it now is.

A mirrored Slack channel's members in Lemma are the pod's members Slack shows
are there, so a person leaving it leaves the mirror (``member_left_channel``),
and a person joining it is seated there before they speak
(``member_joined_channel``) -- once the pod knows who they are.
The bot being taken out (``channel_left`` for a public channel, ``group_left``
for a private one, or a ``member_left_channel`` naming the bot itself) stops
the mirror; being invited back (``member_joined_channel`` naming the bot)
resumes it. A channel shared with another company, or no longer, is
``channel_shared`` / ``channel_unshared``.

A public channel converted to private needs no event of its own: from then on
every message names the channel type ``group``, which flips the mirror
(``workspace_channels``). A ``channel_converted_to_private`` event is read too,
should one arrive.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.modules.agent_surfaces.domain.groups import GroupUpdateKind, ParsedGroupUpdate
from app.modules.agent_surfaces.platforms.common import payload_section, payload_text

_BOT_LEFT = frozenset({"channel_left", "group_left"})
_PEOPLE_MOVING = frozenset({GroupUpdateKind.MEMBER_LEFT, GroupUpdateKind.MEMBER_JOINED})
_BECAME_PRIVATE = frozenset({"channel_converted_to_private"})

#: Every event type read here, for the classifier to send to this path.
SLACK_GROUP_UPDATE_EVENTS = frozenset(
    {
        "member_left_channel",
        "member_joined_channel",
        "channel_shared",
        "channel_unshared",
        *_BOT_LEFT,
        *_BECAME_PRIVATE,
    }
)


def _envelope(payload: Mapping[str, object]) -> Mapping[str, object]:
    for key in ("payload", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict) and nested.get("type") == "event_callback":
            return nested
    return payload


def _bot_user_id(envelope: Mapping[str, object]) -> str:
    authorizations = envelope.get("authorizations")
    for authorization in authorizations if isinstance(authorizations, list) else []:
        user_id = payload_text(authorization, "user_id").strip()
        if user_id:
            return user_id
    return ""


def slack_group_updates(payload: Mapping[str, object]) -> list[ParsedGroupUpdate]:
    """The channel change this event callback reports, if it reports one."""
    envelope = _envelope(payload)
    if envelope.get("type") != "event_callback":
        return []
    event = payload_section(envelope, "event")
    event_type = payload_text(event, "type")
    channel = payload_text(event, "channel").strip()
    if event_type not in SLACK_GROUP_UPDATE_EVENTS or not channel:
        return []
    kind = _kind(event_type, event, bot_user_id=_bot_user_id(envelope))
    if kind is None:
        return []
    return [
        ParsedGroupUpdate(
            platform="SLACK",
            kind=kind,
            external_channel_id=channel,
            tenant_id=payload_text(envelope, "team_id").strip() or None,
            external_user_id=(
                payload_text(event, "user").strip() or None
                if kind in _PEOPLE_MOVING
                else None
            ),
        )
    ]


def _kind(
    event_type: str, event: Mapping[str, object], *, bot_user_id: str
) -> GroupUpdateKind | None:
    user = payload_text(event, "user").strip()
    is_bot = bool(bot_user_id) and user == bot_user_id
    if event_type == "member_left_channel":
        if not user:
            return None
        return GroupUpdateKind.BOT_LEFT if is_bot else GroupUpdateKind.MEMBER_LEFT
    if event_type == "member_joined_channel":
        if not user:
            return None
        return GroupUpdateKind.BOT_RETURNED if is_bot else GroupUpdateKind.MEMBER_JOINED
    if event_type in _BOT_LEFT:
        return GroupUpdateKind.BOT_LEFT
    if event_type in _BECAME_PRIVATE:
        return GroupUpdateKind.BECAME_PRIVATE
    if event_type == "channel_shared":
        return GroupUpdateKind.SHARED
    if event_type == "channel_unshared":
        # Still shared with some other company: nothing changed for the pod.
        return None if event.get("is_ext_shared") else GroupUpdateKind.UNSHARED
    return None


__all__ = ["SLACK_GROUP_UPDATE_EVENTS", "slack_group_updates"]
