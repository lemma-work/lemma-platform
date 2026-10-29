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

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from app.core.authorization.permissions import Permissions, equivalent_permission_ids
from app.modules.pod.domain.pod_entities import PodMemberEntity
from app.modules.pod.domain.roles import PodRole
from app.modules.identity.contracts.orm import OrganizationMember, User
from app.modules.pod.infrastructure.models.pod_models import Pod, PodMember
from app.modules.pod.infrastructure.pod_administration_queries import (
    member_holds,
    members_who_can,
)
from app.modules.pod.infrastructure.pod_repositories import PodMemberRepository


async def pod_member_id(uow, pod_id: UUID, user_id: UUID) -> UUID | None:
    """This user's membership of this pod, or ``None`` if they are not in it.

    The id rather than the row: every caller outside this module wants it to
    compare against something already assigned to a member, and handing back the
    entity invites a second module to start reading fields off it.
    """
    member = await PodMemberRepository(uow).get_by_pod_and_user_id(pod_id, user_id)
    return member.id if member is not None else None


async def pod_members_among(uow, pod_id: UUID, user_ids: Iterable[UUID]) -> set[UUID]:
    """Which of these users are live members of this pod, in one read."""
    wanted = sorted(set(user_ids))
    if not wanted:
        return set()
    found = await uow.session.scalars(
        select(OrganizationMember.user_id)
        .join(PodMember, PodMember.organization_member_id == OrganizationMember.id)
        .join(User, User.id == OrganizationMember.user_id)
        .where(
            PodMember.pod_id == pod_id,
            OrganizationMember.user_id.in_(wanted),
            User.is_active.is_(True),
            User.is_deleted.is_(False),
        )
        .limit(len(wanted))
    )
    return set(found)


async def pod_member_count(uow, pod_id: UUID) -> int:
    """How many people are in this pod right now."""
    counted = await uow.session.scalar(
        select(func.count()).select_from(PodMember).where(PodMember.pod_id == pod_id)
    )
    return int(counted or 0)


async def pod_member_user_id(uow, *, pod_id: UUID, pod_member_id: UUID) -> UUID | None:
    """The user behind this pod membership, or ``None`` if it is not one of
    this pod's -- what a workflow form assigned to a member is asked of."""
    member = await PodMemberRepository(uow).get_by_pod_and_id(pod_id, pod_member_id)
    return member.user_id if member is not None else None


async def pod_member_holds(
    uow, pod_id: UUID, user_id: UUID, permission_id: str
) -> bool:
    """Whether this member's roles in the pod grant ``permission_id``, or a
    permission that implies it.

    For deciding what somebody *else* may be given -- the member a contact's
    chats are to belong to, say -- where there is no request of theirs to
    authorize. A member's own request is authorized the usual way.
    """
    return await member_holds(
        uow.session,
        pod_id=pod_id,
        user_id=user_id,
        permission_ids=sorted(equivalent_permission_ids(permission_id)),
    )


@dataclass(frozen=True, slots=True)
class PodAdministrator:
    user_id: UUID
    pod_member_id: UUID


#: The most administrators one notice to a pod's administrators reaches.
MAX_ADMINISTRATORS_TOLD = 50


async def pod_administrators(uow, pod_id: UUID) -> list[PodAdministrator]:
    """The members who administer the pod: whoever holds ``pod.member.manage``.

    By permission, as the last-administrator rule counts them, so a custom role
    that manages members is as much an administrator as ``POD_ADMIN``.
    """
    return await pod_members_who_hold(uow, pod_id, Permissions.POD_MEMBER_MANAGE)


async def pod_members_who_hold(
    uow, pod_id: UUID, permission_id: str
) -> list[PodAdministrator]:
    """The members whose roles grant ``permission_id``: whom to tell about
    something only they can fix -- the pod's editors (``pod.connector.manage``)
    when a shared connector lost its account. ``MAX_ADMINISTRATORS_TOLD`` at
    most."""
    return [
        PodAdministrator(user_id=user_id, pod_member_id=member_id)
        for member_id, user_id in await members_who_can(
            uow.session,
            pod_id=pod_id,
            permission_id=permission_id,
            limit=MAX_ADMINISTRATORS_TOLD,
        )
    ]


async def pod_name(session, pod_id: UUID) -> str | None:
    """The pod's display name, or ``None`` if it no longer exists."""
    return (
        await session.execute(select(Pod.name).where(Pod.id == pod_id))
    ).scalar_one_or_none()


@dataclass(frozen=True, slots=True)
class PodProfile:
    """The pod as its own agent needs to describe itself.

    `pod_name` answered the brief for a long time, so an agent knew which pod it
    was in and nothing about it: not what the pod is for, which is the one
    sentence the team wrote about their own work, and not when it started, which
    is the tenure the agent's profile page shows to everybody but the agent.
    """

    name: str | None = None
    description: str | None = None
    created_at: datetime | None = None
    #: What the pod owns, said the way its agent should hold itself to it:
    #: the sentence, how it is counted, and where the plan is. ``None`` when it
    #: owns nothing yet.
    owns: str | None = None


def _target(value: float, unit: str | None) -> str:
    number = f"{value:g}"
    if unit == "%":
        return f"{number}%"
    return f"{number} {unit}" if unit else number


def say_what_it_owns(config: object) -> str | None:
    """What the pod owns, as one line of its agent's brief."""
    from app.modules.pod.domain.pod_entities import PLAN_PATH, OutcomeKind, PodConfig

    outcome = PodConfig.from_raw(config).outcome
    if outcome is None:
        return None
    if outcome.kind is OutcomeKind.DATE and outcome.due is not None:
        shape = f"a finish line due {outcome.due.isoformat()}"
    else:
        shape = "a bar held every week"
    held = (
        f", held to {_target(outcome.target, outcome.unit)}"
        if outcome.target is not None
        else ""
    )
    counted = (
        "counted by its measure, one query read for the whole pod"
        if outcome.measure
        else "with no measure yet: when what it counts exists, set one with "
        "`set_what_you_own`"
    )
    return (
        f"{outcome.sentence} It is {shape}{held}, {counted}. How the work gets "
        f"there is in {PLAN_PATH}: read it before changing how you work, and "
        "keep it true."
    )


async def pod_profile(session, pod_id: UUID) -> PodProfile:
    """Name, description and start date in one read.

    An empty profile for a pod that is gone rather than a raise: a brief is
    still worth rendering without one, exactly as with a missing user.
    """
    row = (
        await session.execute(
            select(Pod.name, Pod.description, Pod.created_at, Pod.config).where(
                Pod.id == pod_id
            )
        )
    ).first()
    if row is None:
        return PodProfile()
    name, description, created_at, config = row
    return PodProfile(
        name=name,
        description=description,
        created_at=created_at,
        owns=say_what_it_owns(config),
    )


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
    member = await PodMemberRepository(uow).create(entity)
    await PodRoleService(uow).sync_member_roles(
        pod_id=pod_id,
        pod_member_id=member.id,
        roles=[resolved_role],
        added_by_user_id=None,
    )


async def refuse_pod_role_beyond_inviter(
    uow,
    *,
    pod_id: UUID,
    inviter_user_id: UUID,
    inviter_is_org_owner: bool,
    pod_role: str,
) -> None:
    """Refuse an invitation offering a pod role its author could not confer.

    An invitation that names a pod is a pod-membership grant that happens later,
    and it is bounded exactly as adding the member directly is: the inviter must
    be able to manage that pod's members, and the role must sit inside what they
    hold. Without this an organization editor -- who reaches no pod they are not
    a member of -- could invite a second address to any pod as its administrator.

    The role is resolved as :func:`add_pod_member` will resolve it, so the bound
    is put to the role the invitee would actually receive rather than to a
    spelling that would be quietly downgraded to ``USER``.
    """
    from app.modules.pod.services.pod_role_service import PodRoleService

    try:
        resolved_role = PodRole(pod_role)
    except ValueError:
        resolved_role = PodRole.USER
    await PodRoleService(uow).require_role_manager_bounds(
        pod_id=pod_id,
        requester_user_id=inviter_user_id,
        target_roles=[resolved_role],
        requester_is_org_owner=inviter_is_org_owner,
    )


__all__ = [
    "add_pod_member",
    "pod_invitation_details",
    "pod_member_count",
    "pod_member_id",
    "pod_member_holds",
    "pod_name",
    "pod_organization_id",
    "refuse_pod_role_beyond_inviter",
]


async def leave_organization_pods(
    uow, *, organization_member_id: UUID, user_id: UUID
) -> list[UUID]:
    """Take a departing organization member out of every live pod they are in.

    Through the pod's own removal, one membership at a time, so each one is
    announced as ``PodMemberRemoved`` in this transaction. Deleting the
    organization membership cascades these rows away in the database, and a
    cascade says nothing: every module that lets go of a pod's departed member
    -- their channel seats, the contacts they looked after, their pod accounts
    -- never heard they had gone. Called before that deletion, so there is
    nothing left for the cascade to take silently.

    No last-administrator guard: leaving the organization is not refused for
    the pods it empties of admins, as the cascade never was. A soft-deleted
    pod is skipped; its own cleanup already ran.
    """
    repository = PodMemberRepository(uow)
    left: list[UUID] = []
    after: UUID | None = None
    # A page at a time, by pod: a limit that stopped short would leave the
    # person in every pod past it.
    while True:
        statement = (
            select(PodMember.id, PodMember.pod_id)
            .join(Pod, Pod.id == PodMember.pod_id)
            .where(
                PodMember.organization_member_id == organization_member_id,
                Pod.is_deleted.is_(False),
            )
            .order_by(PodMember.pod_id)
            .limit(_LEAVE_PAGE)
        )
        if after is not None:
            statement = statement.where(PodMember.pod_id > after)
        rows = (await uow.session.execute(statement)).all()
        for pod_member_id, pod_id in rows:
            member = await repository.get(pod_member_id)
            if member is None:
                continue
            member.mark_removed(user_id=user_id)
            if await repository.delete_entity(member):
                left.append(pod_id)
        if len(rows) < _LEAVE_PAGE:
            return left
        after = rows[-1].pod_id


_LEAVE_PAGE = 200
