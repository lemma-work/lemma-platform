"""
Pod module shared test fixtures.
Used by both unit and e2e tests within the pod module.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod.services.pod_service import PodService
from app.modules.identity.services.user_service import UserService
from app.modules.identity.services.organization_service import OrganizationService
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.domain.organization_entities import OrganizationEntity
from app.modules.pod.infrastructure.pod_repositories import (
    PodRepository,
    PodMemberRepository,
)
from app.modules.identity.infrastructure.user_repositories import (
    UserRepository,
)
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)

# Import models to ensure they are registered with SQLAlchemy
from app.modules.connectors.infrastructure.models.account import Account as _Account  # noqa: F401
from app.modules.connectors.infrastructure.models.connector import (
    Connector as _Connector,  # noqa: F401
)


@pytest.fixture
def uow(db_session: AsyncSession) -> SqlAlchemyUnitOfWork:
    """Unit of Work with test database session."""
    return SqlAlchemyUnitOfWork(session=db_session)


@pytest.fixture
def user_service(uow: SqlAlchemyUnitOfWork) -> UserService:
    """User service instance for testing."""
    return UserService(
        user_repository=UserRepository(uow),
        organization_repository=OrganizationRepository(uow),
    )


@pytest.fixture
def organization_service(uow: SqlAlchemyUnitOfWork) -> OrganizationService:
    """Organization service instance for testing."""
    return OrganizationService(
        organization_repository=OrganizationRepository(uow),
        user_repository=UserRepository(uow),
        invitation_accept_base_url="http://localhost:3000",
    )


@pytest.fixture
def pod_service(uow: SqlAlchemyUnitOfWork) -> PodService:
    """Pod service instance for testing."""
    return PodService(
        pod_repository=PodRepository(uow),
        pod_member_repository=PodMemberRepository(uow),
        organization_repository=OrganizationRepository(uow),
    )


@pytest_asyncio.fixture
async def test_user(
    user_service: UserService, sample_user_entity: UserEntity
) -> UserEntity:
    """Creates a test user for pod tests."""
    return await user_service.create_user(sample_user_entity)


@pytest_asyncio.fixture
async def test_org(
    organization_service: OrganizationService,
    test_user: UserEntity,
    sample_org_entity: OrganizationEntity,
) -> OrganizationEntity:
    """Creates a test organization for pod tests."""
    return await organization_service.create_organization(
        sample_org_entity, test_user.id
    )
