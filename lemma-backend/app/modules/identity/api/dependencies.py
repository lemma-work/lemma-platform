"""Identity module dependency injection."""

from typing import Annotated

from fastapi import Depends

from app.core.api.dependencies import UoWDep
from app.core.config import settings
from app.modules.identity.infrastructure.adapters.pod_membership_adapter import (
    SqlAlchemyPodMembershipAdapter,
)
from app.modules.identity.domain.ports import PodMembershipPort
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.identity.infrastructure.user_cache import get_user_cache
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.core.plan_limits import build_plan_limits
from app.modules.identity.contracts.account_deletion import (
    build_account_deletion_guard,
)
from app.modules.identity.services.account_deletion import AccountDeletionService
from app.modules.identity.services.organization_service import OrganizationService
from app.modules.identity.services.user_service import UserService


def get_user_service(
    uow: UoWDep,
) -> UserService:
    """Provide UserService with UoW-backed repositories."""
    return UserService(
        user_repository=UserRepository(uow),
        organization_repository=OrganizationRepository(uow),
        user_cache=get_user_cache(),
    )


def get_organization_service(
    uow: UoWDep,
) -> OrganizationService:
    """Provide OrganizationService with UoW-backed repositories."""
    return OrganizationService(
        organization_repository=OrganizationRepository(uow),
        user_repository=UserRepository(uow),
        invitation_accept_base_url=settings.frontend_url,
        pod_membership_port=SqlAlchemyPodMembershipAdapter(uow),
    )


def get_account_deletion_service(uow: UoWDep) -> AccountDeletionService:
    return AccountDeletionService(
        user_repository=UserRepository(uow),
        organization_repository=OrganizationRepository(uow),
        pod_membership_port=SqlAlchemyPodMembershipAdapter(uow),
        billing_guard=build_account_deletion_guard(uow),
        deployment_bills=build_plan_limits(uow) is not None,
    )


UserServiceDep = Annotated[UserService, Depends(get_user_service)]
AccountDeletionServiceDep = Annotated[
    AccountDeletionService, Depends(get_account_deletion_service)
]
OrganizationServiceDep = Annotated[
    OrganizationService, Depends(get_organization_service)
]


def get_pod_membership_port(uow: UoWDep) -> PodMembershipPort:
    return SqlAlchemyPodMembershipAdapter(uow)


PodMembershipDep = Annotated[PodMembershipPort, Depends(get_pod_membership_port)]
