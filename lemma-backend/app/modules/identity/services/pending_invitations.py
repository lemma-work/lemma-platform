"""Accept the invitations waiting for a person when they first arrive.

Somebody invited to a pod who then signs up another way -- a WhatsApp email
code, the web first-workspace step -- used to be handed a brand-new
organization of their own while the invitation sat pending. The address is
already proven by the time this runs, which is exactly what accepting an
invitation by id checks, so the invitations are honoured here instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.identity.domain.organization_entities import (
    OrganizationInvitationStatus,
)
from app.modules.identity.services.organization_service import OrganizationService

logger = get_logger(__name__)

_MAX_INVITATIONS = 20


@dataclass(frozen=True, slots=True)
class AcceptedInvitation:
    organization_id: UUID
    pod_id: UUID | None


async def accept_pending_invitations(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_service: OrganizationService,
    user_id: UUID,
    email: str,
) -> AcceptedInvitation | None:
    """Accept every pending invitation for this verified address.

    Returns where the person should land: the newest accepted invitation that
    names a pod, else the newest accepted one. Each acceptance runs in its own
    savepoint, so one that cannot be honoured -- a full organization, a pod
    deleted since -- is skipped whole and never stops the person arriving.
    """
    (
        invitations,
        _,
    ) = await organization_service.organization_repository.list_user_invitations(
        user_email=email,
        status=OrganizationInvitationStatus.PENDING,
        limit=_MAX_INVITATIONS,
    )
    accepted: list[AcceptedInvitation] = []
    for invitation in sorted(
        invitations, key=lambda item: item.created_at, reverse=True
    ):
        try:
            async with uow.session.begin_nested():
                await organization_service.accept_invitation(invitation.id, user_id)
        except DomainError as exc:
            logger.warning(
                "identity.first_workspace.invitation_skipped",
                invitation_id=str(invitation.id),
                error_type=type(exc).__name__,
            )
            continue
        logger.info(
            "identity.first_workspace.invitation_accepted",
            invitation_id=str(invitation.id),
            user_id=str(user_id),
        )
        accepted.append(
            AcceptedInvitation(invitation.organization_id, invitation.pod_id)
        )
    if not accepted:
        return None
    return next((item for item in accepted if item.pod_id is not None), accepted[0])
