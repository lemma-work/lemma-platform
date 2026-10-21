"""What Teams says about a chat or channel itself: who was taken out of it.

Teams tells a bot of membership changes in a ``conversationUpdate`` activity:
``membersRemoved`` (a person left or was removed from a group chat, a private
channel -- ``channelMemberRemoved`` -- or the team) and ``membersAdded``. The
bot is among them when it was itself removed or added back, recognised by the
activity's ``recipient``. A person is named by their AAD object id where the
activity carries it, which is how a Teams sender is resolved to a member.

The place is the channel the event names, else the conversation -- a group
chat's own id, or a team's General channel for a team-wide change.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.modules.agent_surfaces.domain.groups import GroupUpdateKind, ParsedGroupUpdate
from app.modules.agent_surfaces.platforms.common import (
    payload_first,
    payload_section,
    payload_text,
)


def _members(value: object) -> list[Mapping[str, object]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _place(activity: Mapping[str, object]) -> str:
    channel_data = payload_section(activity, "channelData")
    channel = payload_text(payload_section(channel_data, "channel"), "id").strip()
    if channel:
        return channel
    conversation = payload_text(payload_section(activity, "conversation"), "id")
    return conversation.split(";messageid=")[0].strip()


def teams_group_updates(activity: Mapping[str, object]) -> list[ParsedGroupUpdate]:
    """Every removal (and the bot's return) one conversationUpdate reports."""
    if activity.get("type") != "conversationUpdate":
        return []
    place = _place(activity)
    if not place:
        return []
    channel_data = payload_section(activity, "channelData")
    tenant = (
        payload_text(payload_section(channel_data, "tenant"), "id")
        or payload_text(payload_section(activity, "conversation"), "tenantId")
    ).strip() or None
    bot = payload_text(payload_section(activity, "recipient"), "id").strip()
    updates: list[ParsedGroupUpdate] = []
    for member in _members(activity.get("membersRemoved")):
        member_id = payload_text(member, "id").strip()
        if bot and member_id == bot:
            updates.append(_update(GroupUpdateKind.BOT_LEFT, place, tenant))
            continue
        person = payload_first(member, "aadObjectId", "id").strip()
        if person:
            updates.append(
                _update(GroupUpdateKind.MEMBER_LEFT, place, tenant, person=person)
            )
    if bot and any(
        payload_text(member, "id").strip() == bot
        for member in _members(activity.get("membersAdded"))
    ):
        updates.append(_update(GroupUpdateKind.BOT_RETURNED, place, tenant))
    return updates


def _update(
    kind: GroupUpdateKind, place: str, tenant: str | None, *, person: str | None = None
) -> ParsedGroupUpdate:
    return ParsedGroupUpdate(
        platform="TEAMS",
        kind=kind,
        external_channel_id=place,
        tenant_id=tenant,
        external_user_id=person,
    )


__all__ = ["teams_group_updates"]
