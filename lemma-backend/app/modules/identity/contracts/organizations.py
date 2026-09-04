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

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
repository layer, and everything importing any identity contract would otherwise
pay for it.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.identity.infrastructure.adapters.email_adapter import (
    SmtpIdentityEmailAdapter,
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


__all__ = [
    "build_identity_email_sender",
    "build_organization_membership",
    "build_user_directory",
    "organization_member_count",
]
