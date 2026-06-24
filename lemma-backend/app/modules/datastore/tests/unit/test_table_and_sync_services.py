from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    DatastoreTableEntity,
)
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreInfrastructureError,
    DatastoreTableNotFoundError,
    DatastoreValidationError,
)
from app.modules.datastore.services.pod_member_sync_service import PodMemberSyncService
from app.modules.datastore.services.table_service import TableService
from app.modules.pod.domain.events import PodMemberAddedEvent, PodMemberRemovedEvent
from app.modules.test_support.authz import allow_all_context, deny_all_context


def _make_table(*, pod_id, name: str = "users") -> DatastoreTableEntity:
    return DatastoreTableEntity(
        pod_id=pod_id,
        table_name=name,
        primary_key_column="id",
        columns=[ColumnSchema(name="name", type=DatastoreDataType.TEXT)],
        enable_rls=True,
    )


async def _create_table_from_entity(
    table_service: TableService,
    entity: DatastoreTableEntity,
    *,
    ctx,
) -> DatastoreTableEntity:
    return await table_service.create_table(
        pod_id=entity.pod_id,
        table_name=entity.table_name,
        primary_key_column=entity.primary_key_column,
        columns=entity.columns,
        config=entity.config,
        enable_rls=entity.enable_rls,
        ctx=ctx,
    )


@pytest.mark.asyncio
async def test_create_table_success_collects_event(
    table_service: TableService,
    table_repository_mock: AsyncMock,
    schema_manager_mock,
):
    user_id = uuid4()
    pod_id = uuid4()
    table = _make_table(pod_id=pod_id, name="customers")

    table_repository_mock.get_by_datastore_and_name.return_value = None
    table_repository_mock.create.return_value = table

    created = await _create_table_from_entity(
        table_service,
        table,
        ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
    )

    assert created == table
    arg = table_repository_mock.create.await_args.args[0]
    created_column_names = {column.name for column in arg.columns}
    assert {"id", "created_at", "updated_at", "user_id", "name"} <= created_column_names
    assert "created_by" not in created_column_names
    assert "updated_by" not in created_column_names
    assert next(column for column in arg.columns if column.name == "user_id").system is True
    events = arg.collect_events()
    assert events[0].event_type == "datastore.table.created"
    schema_manager_mock.create_table.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_table_requires_permission(
    table_service: TableService,
    table_repository_mock: AsyncMock,
):
    user_id = uuid4()
    pod_id = uuid4()

    with pytest.raises(DomainError) as exc_info:
        await _create_table_from_entity(
            table_service,
            _make_table(pod_id=pod_id),
            ctx=deny_all_context(user_id=user_id, pod_id=pod_id),
        )

    assert exc_info.value.status_code == 403
    table_repository_mock.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_table_preserves_domain_error_from_schema_manager(
    table_service: TableService,
    table_repository_mock: AsyncMock,
    schema_manager_mock,
):
    user_id = uuid4()
    pod_id = uuid4()
    table = _make_table(pod_id=pod_id, name="customers")

    table_repository_mock.get_by_datastore_and_name.return_value = None
    table_repository_mock.create.return_value = table
    schema_manager_mock.create_table.side_effect = DatastoreValidationError(
        "UUID auto columns require PostgreSQL UUID support."
    )

    with pytest.raises(DatastoreValidationError):
        await _create_table_from_entity(
            table_service,
            table,
            ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
        )


@pytest.mark.asyncio
async def test_create_table_wraps_unexpected_schema_errors(
    table_service: TableService,
    table_repository_mock: AsyncMock,
    schema_manager_mock,
):
    user_id = uuid4()
    pod_id = uuid4()
    table = _make_table(pod_id=pod_id, name="customers")

    table_repository_mock.get_by_datastore_and_name.return_value = None
    table_repository_mock.create.return_value = table
    schema_manager_mock.create_table.side_effect = RuntimeError("boom")

    with pytest.raises(DatastoreInfrastructureError, match="Failed to create table"):
        await _create_table_from_entity(
            table_service,
            table,
            ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
        )


@pytest.mark.asyncio
async def test_create_table_rejects_explicit_system_timestamp_columns(
    table_service: TableService,
):
    user_id = uuid4()
    pod_id = uuid4()

    with pytest.raises(
        DatastoreValidationError,
        match="System-managed columns must not be declared explicitly",
    ):
        await _create_table_from_entity(
            table_service,
            DatastoreTableEntity(
                pod_id=pod_id,
                table_name="events",
                primary_key_column="id",
                columns=[
                    ColumnSchema(name="title", type=DatastoreDataType.TEXT),
                    ColumnSchema(name="created_at", type=DatastoreDataType.DATETIME),
                ],
                enable_rls=False,
            ),
            ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
        )


@pytest.mark.asyncio
async def test_create_table_accepts_user_and_file_path_column_types(
    table_service: TableService,
    table_repository_mock: AsyncMock,
    schema_manager_mock,
):
    user_id = uuid4()
    pod_id = uuid4()
    table = DatastoreTableEntity(
        pod_id=pod_id,
        table_name="tasks",
        primary_key_column="id",
        columns=[
            ColumnSchema(name="assignee", type=DatastoreDataType.USER, required=True),
            ColumnSchema(name="attachment_path", type=DatastoreDataType.FILE_PATH),
        ],
        enable_rls=False,
    )

    table_repository_mock.get_by_datastore_and_name.return_value = None
    table_repository_mock.create.return_value = table

    created = await _create_table_from_entity(
        table_service,
        table,
        ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
    )

    assert created == table
    arg = table_repository_mock.create.await_args.args[0]
    created_columns = {column.name: column for column in arg.columns}
    assert created_columns["assignee"].type == DatastoreDataType.USER
    assert created_columns["attachment_path"].type == DatastoreDataType.FILE_PATH
    schema_manager_mock.create_table.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_column_duplicate_raises_conflict(
    table_service: TableService,
    table_repository_mock: AsyncMock,
):
    pod_id = uuid4()
    table = DatastoreTableEntity(
        pod_id=pod_id,
        table_name="customers",
        primary_key_column="id",
        columns=[ColumnSchema(name="email", type=DatastoreDataType.TEXT)],
    )

    table_repository_mock.get_by_datastore_and_name.return_value = table

    user_id = uuid4()
    with pytest.raises(DatastoreConflictError):
        await table_service.add_column(
            pod_id,
            table.table_name,
            ColumnSchema(name="email", type=DatastoreDataType.TEXT),
            ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
        )


@pytest.mark.asyncio
async def test_remove_column_missing_raises_not_found(
    table_service: TableService,
    table_repository_mock: AsyncMock,
):
    pod_id = uuid4()
    table = _make_table(pod_id=pod_id, name="customers")

    table_repository_mock.get_by_datastore_and_name.return_value = table

    user_id = uuid4()
    with pytest.raises(DatastoreTableNotFoundError):
        await table_service.remove_column(
            pod_id,
            table.table_name,
            "missing_column",
            ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
        )


@pytest.mark.asyncio
async def test_pod_member_sync_added_creates_reserved_table_and_record():
    table_repo = AsyncMock()
    schema_manager = MagicMock()
    schema_manager.create_table = AsyncMock()
    schema_manager.get_schema_name.return_value = "datastore_x"
    record_service = AsyncMock()

    pod_id = uuid4()
    table_repo.get_by_datastore_and_name.return_value = None
    table_repo.create.return_value = DatastoreTableEntity(
        pod_id=pod_id,
        table_name="reserved_users",
        primary_key_column="user_id",
        columns=[ColumnSchema(name="user_id", type=DatastoreDataType.TEXT)],
        enable_rls=False,
    )
    from app.modules.datastore.domain.errors import DatastoreRecordNotFoundError

    record_service.get_record.side_effect = DatastoreRecordNotFoundError()

    service = PodMemberSyncService(
        table_repository=table_repo,
        schema_manager=schema_manager,
        record_service=record_service,
    )

    event = PodMemberAddedEvent(
        pod_id=pod_id,
        user_id=uuid4(),
        role="POD_VIEWER",
        email="test+user@example.com",
    )

    await service.sync_member_added(event)

    table_repo.create.assert_awaited_once()
    schema_manager.create_table.assert_awaited_once()
    record_service.create_record.assert_awaited_once()


@pytest.mark.asyncio
async def test_pod_member_sync_removed_ignores_missing_record():
    table_repo = AsyncMock()
    schema_manager = MagicMock()
    schema_manager.create_table = AsyncMock()
    schema_manager.get_schema_name.return_value = "datastore_x"
    record_service = AsyncMock()

    pod_id = uuid4()
    table = DatastoreTableEntity(
        pod_id=pod_id,
        table_name="reserved_users",
        primary_key_column="user_id",
        columns=[ColumnSchema(name="user_id", type=DatastoreDataType.TEXT)],
        enable_rls=False,
    )

    table_repo.get_by_datastore_and_name.return_value = table

    from app.modules.datastore.domain.errors import DatastoreRecordNotFoundError

    record_service.delete_record.side_effect = DatastoreRecordNotFoundError()

    service = PodMemberSyncService(
        table_repository=table_repo,
        schema_manager=schema_manager,
        record_service=record_service,
    )

    event = PodMemberRemovedEvent(pod_id=pod_id, user_id=uuid4())

    await service.sync_member_removed(event)

    record_service.delete_record.assert_awaited_once()
