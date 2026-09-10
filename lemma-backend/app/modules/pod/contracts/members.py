"""Facts about one person's membership of one pod.

Replaces `app/composition/workflow_pod.py`, which published `PodMemberRepository`
so `workflow` could construct it. Both callers wanted the same single fact —
this person's pod-member id, or nothing — to check that a form's assignee is the
person submitting it. A repository was three layers more than the question.

`pod_name` is here for the same reason: `agent`'s context brief was reaching
`Pod` itself, through `app/composition/agent_context_models.py`, to select one
column.

**The three below answer identity**, which owns organization invitations and has
to put the invitee in the pod the invitation named. They replace
`app/composition/pod_identity.py`, which built two pod repositories and
`PodRoleService` so it could implement identity's `PodMembershipPort` from
outside both modules -- and in doing so held pod's own rule about what an
unrecognised role name means. Identity's adapter now calls these and types the
result as its own port; the `Protocol` is structural, so pod never names it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.pod.domain.pod_entities import PodMemberEntity
from app.modules.pod.domain.roles import PodRole
from app.modules.pod.infrastructure.models.pod_models import Pod
from app.modules.pod.infrastructure.pod_repositories import PodMemberRepository


async def pod_member_id(uow, pod_id: UUID, user_id: UUID) -> UUID | None:
    """This user's membership of this pod, or ``None`` if they are not in it.

    The id rather than the row: every caller outside this module wants it to
    compare against something already assigned to a member, and handing back the
    entity invites a second module to start reading fields off it.
    """
    member = await PodMemberRepository(uow).get_by_pod_and_user_id(pod_id, user_id)
    return member.id if member is not None else None


async def pod_name(session, pod_id: UUID) -> str | None:
    """The pod's display name, or ``None`` if it no longer exists."""
    return (
        await session.execute(select(Pod.name).where(Pod.id == pod_id))
    ).scalar_one_or_none()


async def pod_organization_id(uow, pod_id: UUID) -> UUID | None:
    """Which organization owns this pod, or ``None`` if it is gone.

    Deleted counts as gone, here and in :func:`pod_invitation_details`: an
    invitation naming a deleted pod must not put anyone in it.
    """
    return (
        await uow.session.execute(
            select(Pod.organization_id).where(
                Pod.id == pod_id, Pod.is_deleted.is_(False)
            )
        )
    ).scalar_one_or_none()


async def pod_invitation_details(
    uow, pod_id: UUID
) -> tuple[str, str | None, UUID] | None:
    """What an invitation email says about the pod: name, description, owner org."""
    row = (
        await uow.session.execute(
            select(Pod.name, Pod.description, Pod.organization_id).where(
                Pod.id == pod_id, Pod.is_deleted.is_(False)
            )
        )
    ).first()
    return (row.name, row.description, row.organization_id) if row else None


async def add_pod_member(
    uow,
    *,
    pod_id: UUID,
    organization_member_id: UUID,
    user_id: UUID,
    user_email: str,
    user_name: str | None,
    pod_role: str,
) -> None:
    """Put this person in this pod, with their roles synced.

    ``pod_role`` arrives as a string because the caller is another module's
    invitation record, and what an unrecognised one means is pod's rule, not the
    caller's: it is a viewer's `USER`, never a refusal, because an invitation
    that has already been accepted must not be left half-applied.

    The member row and the role sync are one operation deliberately. They were
    two calls on two collaborators in the composition root, and a caller holding
    both is a caller that can do the first without the second.
    """
    # Deferred deliberately, and measured: `PodRoleService` pulls five modules
    # (the role repository, the conferral and grant helpers) into every process
    # that imports any pod contract, for a write that runs when somebody accepts
    # an invitation. Everything else in this file is a read and imports at module
    # scope. Same call as `agent/contracts/speech.py`, for the same reason.
    from app.modules.pod.services.pod_role_service import PodRoleService

    try:
        resolved_role = PodRole(pod_role)
    except ValueError:
        resolved_role = PodRole.USER
    message_bus = get_message_bus()
    entity = PodMemberEntity(
        pod_id=pod_id,
        organization_member_id=organization_member_id,
        roles=[resolved_role.value],
        user_id=user_id,
        user_email=user_email,
        user_name=user_name,
    )
    names = user_name.split(" ", 1) if user_name else []
    entity.mark_added(
        user_id=user_id,
        email=user_email,
        first_name=names[0] if names else None,
        last_name=names[1] if len(names) > 1 else None,
    )
    member = await PodMemberRepository(uow, message_bus=message_bus).create(entity)
    await PodRoleService(uow).sync_member_roles(
        pod_id=pod_id,
        pod_member_id=member.id,
        roles=[resolved_role],
        added_by_user_id=None,
    )


__all__ = [
    "add_pod_member",
    "pod_invitation_details",
    "pod_member_id",
    "pod_name",
    "pod_organization_id",
]
