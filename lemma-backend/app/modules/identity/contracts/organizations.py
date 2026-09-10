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

from uuid import UUID

from sqlalchemy import select

from app.modules.identity.domain.organization_entities import OrganizationRole
from app.modules.identity.infrastructure.adapters.email_adapter import (
    SmtpIdentityEmailAdapter,
)
from app.modules.identity.infrastructure.models.organization_models import (
    Organization,
    OrganizationMember,
)
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
    "build_identity_email_sender",
    "build_organization_membership",
    "build_user_directory",
    "organization_exists",
    "organization_member_count",
    "organization_member_role",
    "organization_slug",
]
