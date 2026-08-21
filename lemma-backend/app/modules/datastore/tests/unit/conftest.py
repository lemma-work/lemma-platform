"""Datastore unit test fixtures (mocked ports)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.datastore.services.table_service import TableService

pytestmark = pytest.mark.unit


@pytest.fixture
def table_repository_mock() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def authorization_service_mock() -> AsyncMock:
    service = AsyncMock()
    service.resolve_actor_for_user.side_effect = lambda user_id: user_id
    service.accessible_resource_ids_for_user.return_value = frozenset()
    return service


@pytest.fixture
def schema_manager_mock() -> MagicMock:
    manager = MagicMock()
    manager.create_datastore_schema = AsyncMock()
    manager.drop_datastore_schema = AsyncMock()
    manager.create_table = AsyncMock()
    manager.drop_table = AsyncMock()
    manager.add_column = AsyncMock()
    manager.drop_column = AsyncMock()
    manager.get_schema_name.return_value = "datastore_test"
    return manager


@pytest.fixture
def table_service(
    table_repository_mock: AsyncMock,
    schema_manager_mock: MagicMock,
    authorization_service_mock: AsyncMock,
) -> TableService:
    return TableService(
        table_repository=table_repository_mock,
        schema_manager=schema_manager_mock,
        authorization_service=authorization_service_mock,
    )
