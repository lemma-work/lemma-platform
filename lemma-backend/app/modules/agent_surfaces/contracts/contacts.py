"""What the surfaces hold about a contact, for the module that forgets them.

A trace a contact leaves here besides their conversations: the profile a chat
platform sent for their handle. It goes when the contact is forgotten, in the
caller's transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, exists, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurface,
    AgentSurfaceExternalUser,
)
from app.modules.contacts.contracts import IdentityKind

#: The platform each kind of handle is a sender id on. A host-token handle is
#: the customer's own user, known only to a web widget.
_PLATFORM_FOR_KIND = {
    IdentityKind.PHONE: SurfacePlatform.WHATSAPP.value,
    IdentityKind.TELEGRAM: SurfacePlatform.TELEGRAM.value,
    IdentityKind.EMAIL: SurfacePlatform.RESEND.value,
    IdentityKind.SLACK: SurfacePlatform.SLACK.value,
}


def _sender_ids(kind: IdentityKind, value: str) -> set[str]:
    """The platform user ids a handle can be stored under.

    A number is E.164 in contacts and bare digits as WhatsApp names its sender
    (``wa_id``). A Slack handle is ``{their team}:{their user}``, and the
    profile is kept under the user alone.
    """
    if kind is IdentityKind.SLACK:
        _, _, user = value.partition(":")
        return {user} if user else set()
    return {value, value.removeprefix("+")}


async def forget_contact_senders(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    handles: Sequence[tuple[IdentityKind, str]],
) -> int:
    """Delete the platform profiles stored for these handles.

    Only on platforms the pod has a bot on, and only a profile no Lemma user
    claimed: a handle that is also somebody's account is that person's, not
    something the pod held about a contact.
    """
    platforms = set(
        await uow.session.scalars(
            select(AgentSurface.surface_type)
            .where(AgentSurface.pod_id == pod_id)
            .distinct()
            .limit(len(SurfacePlatform))
        )
    )
    deleted = 0
    for kind, value in handles:
        platform = _PLATFORM_FOR_KIND.get(kind)
        ids = _sender_ids(kind, value)
        if platform is None or platform not in platforms or not ids:
            continue
        where = [
            AgentSurfaceExternalUser.platform == platform,
            AgentSurfaceExternalUser.external_user_id.in_(ids),
            AgentSurfaceExternalUser.resolved_user_id.is_(None),
        ]
        if kind is IdentityKind.SLACK:
            # Kept under the workspace the pod's bot heard them in, which for
            # somebody from another company in a shared channel is not the
            # team their handle names: their own team would miss them.
            where.append(
                exists().where(
                    AgentSurface.pod_id == pod_id,
                    AgentSurface.surface_type == platform,
                    AgentSurface.external_workspace_id
                    == AgentSurfaceExternalUser.tenant_id,
                )
            )
        result = await uow.session.execute(
            delete(AgentSurfaceExternalUser).where(*where)
        )
        deleted += int(result.rowcount or 0)
    return deleted
