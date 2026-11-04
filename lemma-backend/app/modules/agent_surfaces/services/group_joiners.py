"""Who the platform says joined a group, or left it, as the pod knows them.

Somebody joining is seated in the group's channel before they speak; somebody
leaving is taken out. Either way they are found, never made: the member the pod
resolved their account to (its cache of resolved accounts, else -- on Telegram
and WhatsApp -- an account they proved to be theirs), else a contact the pod
already holds for that account. A join opens no contact and nudges nobody:
anybody with a group's link can join it, and Contacts is not to fill with
whoever did. A stranger is seated when they first speak, as before
(``GroupLog``).

A leave is remembered for a few minutes (``recent_leaves``): platform updates
are worked as separate items and can run out of order, and a join worked after
the leave that followed it would seat somebody who is gone -- for a member in a
private group, lasting read access. So a join of somebody who left that recently
waits until they speak.

Seated, they are in the group before they say a word: a member's run in it is
told they read its answers (``group_log._participants``), and the group's
Members tab lists them.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.groups import ParsedGroupUpdate, SurfaceGroup
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.services.verified_surface_identity import (
    resolve_shared_verified_identity,
)
from app.modules.channels.contracts import Principal
from app.modules.contacts.contracts import (
    ContactRef,
    IdentityKind,
    find_contact,
    find_contact_by_user,
)

#: Where an account proved to the shared bot names its person on every bot.
_PROVED_ON_SHARED_BOT = frozenset(
    {SurfacePlatform.TELEGRAM.value, SurfacePlatform.WHATSAPP.value}
)


async def who_moved(
    uow: SqlAlchemyUnitOfWork,
    group: SurfaceGroup,
    update: ParsedGroupUpdate,
    *,
    joining: bool,
) -> Principal | None:
    """The member -- else the contact the pod already holds -- a join or a
    leave names. Joining, a member is one only while in the pod."""
    if not update.external_user_id:
        return None
    user_id = await _account_holder(uow, group, update)
    if user_id is not None and (
        not joining
        or user_id
        in await SqlAlchemySurfaceRoutingResolutionAdapter(uow).pod_members_among(
            group.pod_id, [user_id]
        )
    ):
        return Principal.member(user_id)
    contact = await _known_contact(uow, group, update)
    return Principal.contact(contact.id) if contact is not None else None


async def _account_holder(
    uow: SqlAlchemyUnitOfWork, group: SurfaceGroup, update: ParsedGroupUpdate
) -> UUID | None:
    external_id = update.external_user_id
    assert external_id is not None  # who_moved
    resolved = await ExternalSurfaceUserRepository(uow).resolved_users_by_external_ids(
        platform=group.platform,
        tenant_id=update.tenant_id,
        external_user_ids=[external_id],
    )
    user_id = resolved.get(external_id)
    if user_id is None and group.platform in _PROVED_ON_SHARED_BOT:
        proved = await resolve_shared_verified_identity(uow, _as_account(update))
        user_id = proved.internal_user_id if proved is not None else None
    return user_id


async def _known_contact(
    uow: SqlAlchemyUnitOfWork, group: SurfaceGroup, update: ParsedGroupUpdate
) -> ContactRef | None:
    """The pod's contact for this account, if it has one. Never opened here."""
    external_id = update.external_user_id
    assert external_id is not None  # who_moved
    if group.platform == SurfacePlatform.TELEGRAM.value:
        return await find_contact(
            uow, pod_id=group.pod_id, kind=IdentityKind.TELEGRAM, value=external_id
        )
    if group.platform == SurfacePlatform.SLACK.value:
        return await find_contact_by_user(
            uow, pod_id=group.pod_id, kind=IdentityKind.SLACK, user_id=external_id
        )
    if group.platform == SurfacePlatform.WHATSAPP.value and update.phone:
        return await find_contact(
            uow,
            pod_id=group.pod_id,
            kind=IdentityKind.PHONE,
            value=f"+{update.phone}",
        )
    return None


def _as_account(update: ParsedGroupUpdate) -> ParsedInboundSurfaceEvent:
    """The account a join or leave names, as the event a line of theirs would
    be -- which is what a proved account is looked up by."""
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform(update.platform),
        conversation_type=ConversationType.EXTERNAL_GROUP,
        tenant_id=update.tenant_id,
        external_channel_id=update.external_channel_id,
        external_thread_id=update.external_channel_id or "",
        sender_external_user_id=update.external_user_id,
        sender_phone=update.phone,
        sender_display_name=update.display_name,
        message_text="",
    )
