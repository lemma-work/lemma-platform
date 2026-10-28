"""Who the platform says joined a group, as the pod knows them.

Somebody joining a place is the person who would speak there, so they are
resolved as a speaker is (``GroupLog.speaker``): the member the pod resolved
them to or they proved to be, else a contact opened by their platform handle --
and a member who shares a new contact's name is asked if it is them, never
assumed to be. Somebody the pod cannot name -- a Slack colleague nothing has
resolved yet, a Teams person outside the pod -- is not seated by joining: they
are seated when they first speak, as before.

Seated now, they are in the group before they say a word: a member's run in it
is told they read its answers (``group_log._participants``), and the group's
Members tab lists them.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.groups import ParsedGroupUpdate, SurfaceGroup
from app.modules.agent_surfaces.services.group_log import GroupLog
from app.modules.channels.contracts import Principal, SenderKind


def as_speaker(update: ParsedGroupUpdate) -> ParsedInboundSurfaceEvent | None:
    """The person a join names, as the event a line of theirs would be: who,
    under what name, in which place -- and nothing said."""
    person = update.external_user_id
    if not person or not update.external_channel_id:
        return None
    platform = SurfacePlatform(update.platform)
    return ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=ConversationType.EXTERNAL_GROUP,
        tenant_id=update.tenant_id,
        external_channel_id=update.external_channel_id,
        external_thread_id=update.external_channel_id,
        sender_external_user_id=person,
        sender_aad_object_id=person if platform is SurfacePlatform.TEAMS else None,
        sender_phone=update.phone,
        sender_display_name=update.display_name,
        message_text="",
    )


async def who_joined(
    uow: SqlAlchemyUnitOfWork, group: SurfaceGroup, update: ParsedGroupUpdate
) -> Principal | None:
    """The member -- else the contact -- the platform says joined the group."""
    event = as_speaker(update)
    if event is None:
        return None
    sender = await GroupLog(uow).speaker(group, event)
    if sender.kind is SenderKind.MEMBER and sender.user_id is not None:
        return Principal.member(sender.user_id)
    if sender.kind is SenderKind.CONTACT and sender.contact_id is not None:
        return Principal.contact(sender.contact_id)
    return None
