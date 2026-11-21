"""Identity's organization surface: what it builds for others, and what it counts.

**The factories.** `pod/domain/ports.py` has declared `OrganizationMembershipPort`
for a long time while `pod` imported `OrganizationRepository` anyway, through
`app/composition/pod_identity_wiring.py`. The port was doing no work: the type
said "anything shaped like this" and the import said "this exact class". Identity
does **not** import the port — a `Protocol` is checked structurally, so the
implementation satisfies it without naming it, which is what lets the arrow point
inward without either module reaching the other's internals. The consumer binds
these in its `api/dependencies.py` and types the result as its own port.

**The count** answers a question the domain event deliberately does not carry.
`OrganizationMemberAddedEvent` names the member and the organization and stops
there: a size written at publish time is already stale by the time a consumer
reads it, and a consumer bucketing the organization it just grew needs the number
as it is now.

**The reads** are single columns, and each replaces a caller that had reached
identity's tables to get one. `organization_member_role` came out of
`app/composition/identity_notifications.py`, which published the same membership
lookup twice with a different policy baked into each; `organization_exists` and
`organization_slug` came out of `connector_identity.py` and `usage_limits.py`,
which held a `select()` and an `OrganizationRepository` respectively against
identity's schema from a third module's file.

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
repository layer, and everything importing any identity contract would otherwise
pay for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.modules.identity.domain.organization_entities import (
    OrganizationJoinPolicy,
    OrganizationRole,
    may_self_join,
)
from app.modules.identity.infrastructure.adapters.email_adapter import (
    SmtpIdentityEmailAdapter,
)
from app.modules.identity.infrastructure.models.organization_models import (
    Organization,
    OrganizationMember,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.identity.infrastructure.user_repositories import UserRepository


def build_organization_membership(uow) -> OrganizationRepository:
    """Satisfies a consumer's organization-membership port."""
    return OrganizationRepository(uow)


def build_user_directory(uow) -> UserRepository:
    """Satisfies a consumer's user-lookup port."""
    return UserRepository(uow)


def build_identity_email_sender() -> SmtpIdentityEmailAdapter:
    """Satisfies a consumer's transactional-email port."""
    return SmtpIdentityEmailAdapter()


async def organization_member_count(uow, organization_id: UUID) -> int:
    """How many people are in this organization right now."""
    return await OrganizationRepository(uow).count_members(organization_id)


async def organization_exists(uow, organization_id: UUID) -> bool:
    """Whether this organization is one identity knows about."""
    return (
        await uow.session.execute(
            select(Organization.id).where(Organization.id == organization_id)
        )
    ).scalar_one_or_none() is not None


async def organization_slug(uow, organization_id: UUID) -> str | None:
    """The organization's handle, or ``None`` if it no longer exists.

    One column, so a caller keying deployment configuration off the handle does
    not load the row -- and does not end up holding an `OrganizationEntity` it
    reads one attribute of.
    """
    return (
        await uow.session.execute(
            select(Organization.slug).where(Organization.id == organization_id)
        )
    ).scalar_one_or_none()


async def organization_name(uow, organization_id: UUID) -> str | None:
    """What the organization is called, or ``None`` if it no longer exists."""
    return (
        await uow.session.execute(
            select(Organization.name).where(Organization.id == organization_id)
        )
    ).scalar_one_or_none()


@dataclass(frozen=True)
class OrganizationAtHandle:
    """The organization a handle names, and where one person stands with it."""

    organization_id: UUID
    #: Their membership row, or ``None`` when they are not in it.
    member_id: UUID | None
    role: OrganizationRole | None
    #: Whether they could join it without being asked in. True for a member.
    may_join: bool


async def organization_at_handle(
    uow, *, handle: str, user_id: UUID
) -> OrganizationAtHandle | None:
    """The organization whose handle this is, as this person stands with it.

    ``None`` when no organization has the handle. Whether that may be told
    apart from one the person cannot reach is the caller's policy: this says
    what is true, for a caller about to decide what to reveal.
    """
    organization = (
        await uow.session.execute(
            select(
                Organization.id, Organization.join_policy, Organization.email_domain
            ).where(Organization.slug == handle)
        )
    ).one_or_none()
    if organization is None:
        return None
    member = (
        await uow.session.execute(
            select(OrganizationMember.id, OrganizationMember.role).where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.organization_id == organization.id,
            )
        )
    ).one_or_none()
    if member is not None:
        return OrganizationAtHandle(
            organization_id=organization.id,
            member_id=member.id,
            role=OrganizationRole(member.role),
            may_join=True,
        )
    person = (
        await uow.session.execute(
            select(User.email, User.is_verified).where(User.id == user_id)
        )
    ).one_or_none()
    return OrganizationAtHandle(
        organization_id=organization.id,
        member_id=None,
        role=None,
        may_join=person is not None
        and may_self_join(
            join_policy=OrganizationJoinPolicy(organization.join_policy),
            email_domain=organization.email_domain,
            user_email=person.email,
            user_verified=person.is_verified,
        ),
    )


class OrganizationsByHandle:
    """``organization_at_handle`` as an object a consumer's port can name."""

    def __init__(self, uow) -> None:
        self._uow = uow

    async def at_handle(
        self, handle: str, user_id: UUID
    ) -> OrganizationAtHandle | None:
        return await organization_at_handle(self._uow, handle=handle, user_id=user_id)


async def organization_member_role(
    uow, *, user_id: UUID, organization_id: UUID
) -> OrganizationRole | None:
    """This person's role in this organization, or ``None`` if they are not in it.

    The role, not a yes/no: `app/composition/identity_notifications.py` asked
    this same question twice with two different answers baked in -- "is a
    member" for one caller and "is an owner or an editor" for another. Which
    roles may do a thing is the *caller's* policy, and identity was carrying two
    copies of it. Identity answers what the role is; the caller decides what it
    permits.

    One column and no `joinedload`: `OrganizationRepository.get_member` eager-loads
    the user row to build an entity, which is a join and a second table for a
    question about a single string.
    """
    stored = (
        await uow.session.execute(
            select(OrganizationMember.role).where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    if stored is None:
        return None
    # The column is a `String(50)` typed `Mapped[OrganizationRole]`, so a
    # single-column select hands back the raw string; only `to_entity()` coerces.
    return OrganizationRole(stored)


__all__ = [
    "OrganizationAtHandle",
    "OrganizationsByHandle",
    "build_identity_email_sender",
    "build_organization_membership",
    "build_user_directory",
    "organization_at_handle",
    "organization_exists",
    "organization_member_count",
    "organization_member_role",
    "organization_name",
    "organization_slug",
]


async def preferred_organization_membership(
    uow, *, user_id: UUID, preferred_organization_id: UUID | None
) -> tuple[UUID, UUID] | None:
    """Where to put something new for this person: (organization, membership).

    Prefers the organization the caller names -- a workspace created from a
    company's chat installation belongs with that company -- and otherwise
    takes their oldest membership. None means they belong to no organization at
    all, which callers report rather than paper over.
    """
    preferred = None
    if preferred_organization_id is not None:
        preferred = await uow.session.scalar(
            select(OrganizationMember).where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.organization_id == preferred_organization_id,
            )
        )
    membership = preferred or await uow.session.scalar(
        select(OrganizationMember)
        .where(OrganizationMember.user_id == user_id)
        .order_by(OrganizationMember.created_at, OrganizationMember.id)
        .limit(1)
    )
    if membership is None:
        return None
    return membership.organization_id, membership.id


#: How many of a person's organization memberships any one caller will look at.
#: Belonging to more organizations than this is not something the product
#: supports meaningfully, and a caller that silently read an unbounded set here
#: would be doing a table scan on behalf of whoever holds the record.
MAX_MEMBERSHIPS_READ = 200


async def organization_member_ids_for_user(
    uow, *, user_id: UUID, limit: int = MAX_MEMBERSHIPS_READ
) -> list[UUID]:
    """The membership rows this person holds, for callers that key off them.

    Ordered and capped rather than "all of them": the set is small in practice
    but nothing in the schema says so, and an uncapped read is the caller's
    problem the day someone's account proves otherwise.
    """
    rows = await uow.session.scalars(
        select(OrganizationMember.id)
        .where(OrganizationMember.user_id == user_id)
        .order_by(OrganizationMember.created_at, OrganizationMember.id)
        .limit(limit)
    )
    return list(rows)
