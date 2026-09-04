"""Identity's side of the ports other modules declare for it.

`pod/domain/ports.py` has declared `OrganizationMembershipPort` for a long time
and `pod` imported `OrganizationRepository` anyway, through
`app/composition/pod_identity_wiring.py`. The port was doing no work: the type
said "anything shaped like this" and the import said "this exact class".

A factory rather than the class, and identity does not import the port. A
`Protocol` is checked structurally, so the implementation satisfies it without
naming it — which is what lets the arrow point inward without either module
importing the other's internals. The consumer binds this in its
`api/dependencies.py` and types the result as its own port.
"""

from __future__ import annotations

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


__all__ = [
    "build_identity_email_sender",
    "build_organization_membership",
    "build_user_directory",
]
