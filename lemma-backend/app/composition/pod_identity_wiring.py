"""Identity adapters and ORM join targets used by pod persistence."""

from app.modules.identity.infrastructure.adapters.email_adapter import (
    SmtpIdentityEmailAdapter,
)
from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.identity.infrastructure.user_repositories import UserRepository


def create_identity_email_port() -> SmtpIdentityEmailAdapter:
    return SmtpIdentityEmailAdapter()


def create_organization_repository(uow) -> OrganizationRepository:
    return OrganizationRepository(uow)


def create_user_repository(uow) -> UserRepository:
    return UserRepository(uow)


__all__ = [
    "OrganizationMember",
    "User",
    "create_identity_email_port",
    "create_organization_repository",
    "create_user_repository",
]
