"""Identity module unit test fixtures (mocked ports)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.identity.services.organization_service import OrganizationService
from app.modules.identity.services.user_service import UserService
from app.modules.identity.domain.user_entities import UserEntity

pytestmark = pytest.mark.unit


@pytest.fixture
def user_repository_mock() -> AsyncMock:
    mock = AsyncMock()
    mock.get.return_value = UserEntity(email="test+owner@example.com")
    mock.get_by_email.return_value = None
    mock.get_id_by_mobile_digits.return_value = None
    mock.get_id_by_telegram_lower.return_value = None
    return mock


@pytest.fixture
def organization_repository_mock() -> AsyncMock:
    mock = AsyncMock()
    mock.get_by_name.return_value = None
    mock.get_by_slug.return_value = None
    mock.get_email_domain_org.return_value = None
    mock.list_auto_join_organizations_by_email_domain.return_value = ([], None)
    return mock


@pytest.fixture
def user_cache_mock() -> AsyncMock:
    mock = AsyncMock()
    mock.get.return_value = None
    return mock


@pytest.fixture
def pod_membership_port_mock() -> AsyncMock:
    mock = AsyncMock()
    mock.get_pod_invitation_details.return_value = None
    return mock


@pytest.fixture
def user_service(
    user_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
    user_cache_mock: AsyncMock,
) -> UserService:
    return UserService(
        user_repository=user_repository_mock,
        organization_repository=organization_repository_mock,
        user_cache=user_cache_mock,
    )


@pytest.fixture
def organization_service(
    organization_repository_mock: AsyncMock,
    user_repository_mock: AsyncMock,
    pod_membership_port_mock: AsyncMock,
) -> OrganizationService:
    return OrganizationService(
        organization_repository=organization_repository_mock,
        user_repository=user_repository_mock,
        invitation_accept_base_url="http://localhost:3000",
        pod_membership_port=pod_membership_port_mock,
    )
