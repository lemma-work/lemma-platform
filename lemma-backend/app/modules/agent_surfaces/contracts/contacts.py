"""What the surfaces hold about a contact, for the module that forgets them.

Three traces a contact leaves here besides their conversations: the web chat
sessions and one-time codes that named them, the profile a chat platform sent
for their handle, and the questions of theirs a bot passed on to a member. Each
goes when the contact is forgotten, in the caller's transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select, update

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurface,
    AgentSurfaceExternalUser,
    NotificationModel,
)
from app.modules.contacts.contracts import IdentityKind

#: Where a notification's words stood, once the person they were about is gone.
REDACTED_TITLE = "Removed"

#: The platform each kind of handle is a sender id on. A host-token handle is
#: the customer's own user, known only to a web widget.
_PLATFORM_FOR_KIND = {
    IdentityKind.PHONE: SurfacePlatform.WHATSAPP.value,
    IdentityKind.TELEGRAM: SurfacePlatform.TELEGRAM.value,
    IdentityKind.EMAIL: SurfacePlatform.RESEND.value,
}


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
        if platform is None or platform not in platforms:
            continue
        result = await uow.session.execute(
            delete(AgentSurfaceExternalUser).where(
                AgentSurfaceExternalUser.platform == platform,
                AgentSurfaceExternalUser.external_user_id == value,
                AgentSurfaceExternalUser.resolved_user_id.is_(None),
            )
        )
        deleted += int(result.rowcount or 0)
    return deleted


async def redact_questions_from(
    uow: SqlAlchemyUnitOfWork, *, conversation_ids: Sequence[UUID]
) -> int:
    """Blank the words of every notification asked from these conversations.

    Run before the conversations go: deleting them only clears the pointer, and
    a member's inbox would keep the contact's question, and the name they gave,
    verbatim.
    """
    if not conversation_ids:
        return 0
    result = await uow.session.execute(
        update(NotificationModel)
        .where(NotificationModel.origin_conversation_id.in_(list(conversation_ids)))
        .values(
            title=REDACTED_TITLE,
            body="",
            background_instruction=None,
            asked_by_name=None,
            response_summary=None,
            response_data=None,
            action=None,
        )
    )
    return int(result.rowcount or 0)
