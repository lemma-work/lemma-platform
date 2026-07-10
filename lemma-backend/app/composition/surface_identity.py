"""Identity and pod persistence joins used by surface routing."""

from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.pod.infrastructure.models.pod_models import Pod, PodMember


def create_surface_user_repository(uow) -> UserRepository:
    return UserRepository(uow)


__all__ = [
    "OrganizationMember",
    "Pod",
    "PodMember",
    "User",
    "UserRepository",
    "create_surface_user_repository",
]
