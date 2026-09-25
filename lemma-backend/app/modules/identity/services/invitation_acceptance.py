"""The writes an invitation acceptance makes, once it has been judged valid.

Split from ``OrganizationService.accept_invitation``, which decides *whether*
an acceptance may happen; this is *what* it does. The one branch worth naming:
somebody already in the organization who is invited to a pod gets the pod, and
the seat they already hold is not taken a second time.
"""

from __future__ import annotations

from app.modules.identity.domain.organization_entities import (
    OrganizationInvitationEntity,
    OrganizationMemberEntity,
)
from app.modules.identity.services.membership_rules import PodGrant


async def apply_accepted_invitation(
    *,
    organization_repository,
    pod_membership_port,
    invitation: OrganizationInvitationEntity,
    user,
    organization_name: str,
    existing_member: OrganizationMemberEntity | None,
    pod_grant: PodGrant | None,
) -> OrganizationMemberEntity:
    invitation.mark_accepted(
        accepted_user_id=user.id,
        accepted_email=str(user.email),
        organization_name=organization_name,
    )
    if existing_member is not None:
        await organization_repository.update_invitation(invitation)
        member = existing_member
    else:
        await organization_repository.lock_seats(invitation.organization_id)
        await organization_repository.update_invitation(invitation)
        member = await organization_repository.add_member(
            OrganizationMemberEntity(
                user_id=user.id,
                organization_id=invitation.organization_id,
                role=invitation.role,
            )
        )
    if pod_grant is None:
        return member
    if existing_member is not None and await pod_membership_port.is_pod_member(
        pod_id=pod_grant.pod_id, user_id=user.id
    ):
        return member
    user_name = " ".join(part for part in [user.first_name, user.last_name] if part)
    await pod_membership_port.add_member_to_pod(
        pod_id=pod_grant.pod_id,
        organization_member_id=member.id,
        user_id=user.id,
        user_email=str(user.email),
        user_name=user_name or None,
        pod_role=pod_grant.pod_role,
    )
    return member
