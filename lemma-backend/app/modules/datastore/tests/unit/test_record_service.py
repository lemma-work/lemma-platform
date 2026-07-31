from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.permissions import Permissions
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    DatastoreTableEntity,
)
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.domain.events import (
    DATASTORE_EVENTS_STREAM,
    DatastoreRecordEvent,
    DatastoreRecordOperation,
)
from app.modules.datastore.services.record_service import RecordService
from app.modules.datastore.services.record_validator import convert_record
from app.modules.datastore.services.table_context import TableContext


def _table_context() -> TableContext:
    table = DatastoreTableEntity(
        pod_id=uuid4(),
        table_name="expenses",
        primary_key_column="id",
        columns=[
            ColumnSchema(
                name="id",
                type=DatastoreDataType.UUID,
                required=True,
                unique=True,
                auto=True,
            ),
            ColumnSchema(name="merchant", type=DatastoreDataType.TEXT, required=True),
            ColumnSchema(
                name="created_at",
                type=DatastoreDataType.DATETIME,
                auto=True,
                system=True,
            ),
            ColumnSchema(
                name="updated_at",
                type=DatastoreDataType.DATETIME,
                auto=True,
                system=True,
            ),
            ColumnSchema(
                name="user_id",
                type=DatastoreDataType.UUID,
                required=True,
                auto=True,
                system=True,
            ),
        ],
        enable_rls=True,
    )
    return TableContext.from_table_entity(table, "pod_test")


def _events_enabled_context() -> TableContext:
    table = DatastoreTableEntity(
        pod_id=uuid4(),
        table_name="expenses",
        primary_key_column="id",
        columns=[
            ColumnSchema(
                name="id",
                type=DatastoreDataType.UUID,
                required=True,
                unique=True,
                auto=True,
            ),
            ColumnSchema(name="merchant", type=DatastoreDataType.TEXT, required=True),
        ],
        enable_rls=False,
    )
    return TableContext.from_table_entity(table, "pod_test", events_enabled=True)


def _events_enabled_rls_context() -> TableContext:
    table = DatastoreTableEntity(
        pod_id=uuid4(),
        table_name="expenses",
        primary_key_column="id",
        columns=[
            ColumnSchema(
                name="id",
                type=DatastoreDataType.UUID,
                required=True,
                unique=True,
                auto=True,
            ),
            ColumnSchema(name="merchant", type=DatastoreDataType.TEXT, required=True),
            ColumnSchema(
                name="user_id",
                type=DatastoreDataType.UUID,
                required=True,
                auto=True,
                system=True,
            ),
        ],
        enable_rls=True,
    )
    return TableContext.from_table_entity(table, "pod_test", events_enabled=True)


async def test_create_record_emits_record_event_on_unified_stream():
    ctx = _events_enabled_context()
    user_id = uuid4()
    record_id = str(uuid4())
    record_repository = AsyncMock()
    record_repository.create_record.return_value = type(
        "StoredRecord",
        (),
        {"user_id": uuid4(), "id": record_id, "data": {"merchant": "Hotel"}},
    )()
    message_bus = AsyncMock()
    service = RecordService(record_repository=record_repository, message_bus=message_bus)

    await service.create_record(ctx, {"merchant": "Hotel"}, user_id)

    stream, event = message_bus.publish.await_args.args
    assert stream == DATASTORE_EVENTS_STREAM
    assert isinstance(event, DatastoreRecordEvent)
    assert event.event_type == "datastore.record.insert"
    assert event.operation == DatastoreRecordOperation.INSERT
    assert event.actor_id == user_id
    assert event.table_name == "expenses"
    assert event.record_id == record_id


async def test_production_record_event_is_staged_by_repository_not_published_after_commit():
    ctx = _events_enabled_context()
    user_id = uuid4()
    record_id = str(uuid4())
    stored = type(
        "StoredRecord",
        (),
        {"user_id": user_id, "id": record_id, "data": {"merchant": "Hotel"}},
    )()
    staged_events = []
    record_repository = AsyncMock()

    async def create_record(_ctx, _data, _user_id, *, event_factory):
        staged_events.append(event_factory(stored))
        return stored

    record_repository.create_record.side_effect = create_record
    message_bus = AsyncMock()
    service = RecordService(
        record_repository=record_repository,
        message_bus=message_bus,
        transactional_events=True,
    )

    await service.create_record(ctx, {"merchant": "Hotel"}, user_id)

    message_bus.publish.assert_not_called()
    assert len(staged_events) == 1
    assert staged_events[0].record_id == record_id
    assert staged_events[0].operation == DatastoreRecordOperation.INSERT


async def test_create_record_skips_event_when_events_disabled():
    ctx = _table_context()  # events_enabled defaults to False
    record_repository = AsyncMock()
    record_repository.create_record.return_value = type(
        "StoredRecord",
        (),
        {"user_id": uuid4(), "id": str(uuid4()), "data": {"merchant": "Hotel"}},
    )()
    message_bus = AsyncMock()
    service = RecordService(record_repository=record_repository, message_bus=message_bus)

    await service.create_record(ctx, {"merchant": "Hotel"}, uuid4())

    message_bus.publish.assert_not_called()


async def test_update_and_delete_emit_record_events():
    ctx = _events_enabled_context()
    user_id = uuid4()
    record_id = str(uuid4())
    record_repository = AsyncMock()
    record_repository.update_record.return_value = type(
        "StoredRecord",
        (),
        {"user_id": uuid4(), "id": record_id, "data": {"merchant": "Retreat"}},
    )()
    record_repository.delete_record.return_value = True
    message_bus = AsyncMock()
    service = RecordService(record_repository=record_repository, message_bus=message_bus)

    await service.update_record(ctx, record_id, {"merchant": "Retreat"}, user_id)
    _, update_event = message_bus.publish.await_args.args
    assert update_event.operation == DatastoreRecordOperation.UPDATE
    assert update_event.event_type == "datastore.record.update"
    assert update_event.actor_id == user_id

    await service.delete_record(ctx, record_id, user_id)
    _, delete_event = message_bus.publish.await_args.args
    assert delete_event.operation == DatastoreRecordOperation.DELETE
    assert delete_event.event_type == "datastore.record.delete"


async def test_bulk_create_emits_one_insert_event_per_row():
    ctx = _events_enabled_context()
    user_id = uuid4()
    record_repository = AsyncMock()
    record_repository.bulk_create_records.return_value = 2
    message_bus = AsyncMock()
    service = RecordService(record_repository=record_repository, message_bus=message_bus)

    await service.bulk_create_records(
        ctx,
        [{"merchant": "Hotel"}, {"merchant": "Cafe"}],
        user_id,
    )

    assert message_bus.publish.await_count == 2
    for call in message_bus.publish.await_args_list:
        stream, event = call.args
        assert stream == DATASTORE_EVENTS_STREAM
        assert event.operation == DatastoreRecordOperation.INSERT
        assert event.actor_id == user_id


async def test_record_event_carries_row_owner_for_rls_table():
    """RLS tables tag events with the row owner so change subscribers can scope
    delivery to that user without a database read."""
    ctx = _events_enabled_rls_context()
    caller = uuid4()
    owner = uuid4()
    record_id = str(uuid4())
    record_repository = AsyncMock()
    record_repository.create_record.return_value = type(
        "StoredRecord",
        (),
        {"user_id": owner, "id": record_id, "data": {"merchant": "Hotel"}},
    )()
    message_bus = AsyncMock()
    service = RecordService(record_repository=record_repository, message_bus=message_bus)

    await service.create_record(ctx, {"merchant": "Hotel"}, caller)

    _, event = message_bus.publish.await_args.args
    assert event.owner_user_id == owner
    assert event.actor_id == caller


async def test_record_event_omits_owner_for_non_rls_table():
    """Shared (non-RLS) rows carry no owner, so subscribers fan them out to every
    member who can read the table."""
    ctx = _events_enabled_context()  # enable_rls=False
    record_repository = AsyncMock()
    record_repository.create_record.return_value = type(
        "StoredRecord",
        (),
        {"user_id": uuid4(), "id": str(uuid4()), "data": {"merchant": "Hotel"}},
    )()
    message_bus = AsyncMock()
    service = RecordService(record_repository=record_repository, message_bus=message_bus)

    await service.create_record(ctx, {"merchant": "Hotel"}, uuid4())

    _, event = message_bus.publish.await_args.args
    assert event.owner_user_id is None


async def test_delete_record_event_owner_defaults_to_caller_on_rls_table():
    """A self-scoped RLS delete owns its own row, so the event is tagged to the
    deleting user."""
    ctx = _events_enabled_rls_context()
    caller = uuid4()
    record_id = str(uuid4())
    record_repository = AsyncMock()
    record_repository.delete_record.return_value = True
    message_bus = AsyncMock()
    service = RecordService(record_repository=record_repository, message_bus=message_bus)

    await service.delete_record(ctx, record_id, caller)

    _, event = message_bus.publish.await_args.args
    assert event.operation == DatastoreRecordOperation.DELETE
    assert event.owner_user_id == caller


async def test_create_record_ignores_user_supplied_timestamps():
    ctx = _table_context()
    record_repository = AsyncMock()
    stored = {
        "id": str(uuid4()),
        "merchant": "Hotel",
        "user_id": str(uuid4()),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    record_repository.create_record.return_value = type(
        "StoredRecord",
        (),
        {"user_id": uuid4(), "id": stored["id"], "data": stored},
    )()
    service = RecordService(record_repository=record_repository, message_bus=AsyncMock())

    await service.create_record(
        ctx,
        {
            "merchant": "Hotel",
            "created_at": "2026-04-01T00:00:00Z",
            "updated_at": "not-even-a-date",
        },
        uuid4(),
    )

    _, sanitized_payload, _ = record_repository.create_record.await_args.args
    assert sanitized_payload == {"merchant": "Hotel"}


async def test_update_record_ignores_user_supplied_timestamps():
    ctx = _table_context()
    record_repository = AsyncMock()
    stored = {
        "id": str(uuid4()),
        "merchant": "Hotel",
        "user_id": str(uuid4()),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    record_repository.update_record.return_value = type(
        "StoredRecord",
        (),
        {"user_id": uuid4(), "id": stored["id"], "data": stored},
    )()
    service = RecordService(record_repository=record_repository, message_bus=AsyncMock())

    await service.update_record(
        ctx,
        stored["id"],
        {
            "merchant": "Retreat",
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2020-01-01T00:00:00Z",
        },
        uuid4(),
    )

    _, _, sanitized_payload, _ = record_repository.update_record.await_args.args
    assert sanitized_payload == {"merchant": "Retreat"}


async def test_rls_record_mutations_use_record_write_action():
    auth_context = AsyncMock()
    ctx = _table_context()
    user_id = uuid4()
    record_repository = AsyncMock()
    record_repository.create_record.return_value = type(
        "StoredRecord",
        (),
        {"user_id": uuid4(), "id": str(uuid4()), "data": {"merchant": "Cafe", "user_id": str(user_id)}},
    )()
    authorization_service = AsyncMock()
    authorization_service.resolve_resource_id_by_name.return_value = uuid4()
    service = RecordService(
        record_repository=record_repository,
        message_bus=AsyncMock(),
        authorization_service=authorization_service,
    )

    token = set_current_context(auth_context)
    try:
        await service.create_record(ctx, {"merchant": "Cafe"}, user_id)
    finally:
        reset_current_context(token)

    assert auth_context.require.await_args.args[0] == Permissions.DATASTORE_RECORD_WRITE


async def test_non_rls_record_mutations_require_record_write_action():
    # Data writes are governed by record.write regardless of RLS; table.update
    # is schema-only and must not gate record writes.
    auth_context = AsyncMock()
    ctx = _table_context()
    ctx.enable_rls = False
    user_id = uuid4()
    record_repository = AsyncMock()
    record_repository.create_record.return_value = type(
        "StoredRecord",
        (),
        {"user_id": uuid4(), "id": str(uuid4()), "data": {"merchant": "Cafe"}},
    )()
    authorization_service = AsyncMock()
    authorization_service.resolve_resource_id_by_name.return_value = uuid4()
    service = RecordService(
        record_repository=record_repository,
        message_bus=AsyncMock(),
        authorization_service=authorization_service,
    )

    token = set_current_context(auth_context)
    try:
        await service.create_record(ctx, {"merchant": "Cafe"}, user_id)
    finally:
        reset_current_context(token)

    assert auth_context.require.await_args.args[0] == Permissions.DATASTORE_RECORD_WRITE


async def test_table_context_converts_user_reference_column_to_uuid():
    assignee_id = uuid4()
    table = DatastoreTableEntity(
        pod_id=uuid4(),
        table_name="tasks",
        primary_key_column="id",
        columns=[
            ColumnSchema(
                name="id",
                type=DatastoreDataType.UUID,
                required=True,
                unique=True,
                auto=True,
            ),
            ColumnSchema(name="title", type=DatastoreDataType.TEXT, required=True),
            ColumnSchema(name="assignee", type=DatastoreDataType.USER, required=True),
            ColumnSchema(name="artifact_path", type=DatastoreDataType.FILE_PATH),
        ],
        enable_rls=False,
    )
    ctx = TableContext.from_table_entity(table, "pod_test")

    converted = convert_record(
        ctx.columns,
        {
            "title": "Review release notes",
            "assignee": str(assignee_id),
            "artifact_path": "/docs/release-notes.md",
        },
    )

    assert converted["assignee"] == UUID(str(assignee_id))
    assert converted["artifact_path"] == "/docs/release-notes.md"


async def test_create_record_rejects_unknown_user_reference():
    missing_user_id = uuid4()
    table = DatastoreTableEntity(
        pod_id=uuid4(),
        table_name="tasks",
        primary_key_column="id",
        columns=[
            ColumnSchema(
                name="id",
                type=DatastoreDataType.UUID,
                required=True,
                unique=True,
                auto=True,
            ),
            ColumnSchema(name="title", type=DatastoreDataType.TEXT, required=True),
            ColumnSchema(name="assignee", type=DatastoreDataType.USER, required=True),
        ],
        enable_rls=False,
    )
    ctx = TableContext.from_table_entity(table, "pod_test")
    record_repository = AsyncMock()
    user_repository = AsyncMock()
    user_repository.get.return_value = None
    service = RecordService(
        record_repository=record_repository,
        message_bus=AsyncMock(),
        user_repository=user_repository,
    )

    with pytest.raises(
        DatastoreValidationError,
        match="User does not exist for column 'assignee'",
    ):
        await service.create_record(
            ctx,
            {"title": "Review release notes", "assignee": str(missing_user_id)},
            uuid4(),
        )

    user_repository.get.assert_awaited_once_with(missing_user_id)
    record_repository.create_record.assert_not_called()


async def test_update_record_rejects_unknown_user_reference():
    missing_user_id = uuid4()
    table = DatastoreTableEntity(
        pod_id=uuid4(),
        table_name="tasks",
        primary_key_column="id",
        columns=[
            ColumnSchema(
                name="id",
                type=DatastoreDataType.UUID,
                required=True,
                unique=True,
                auto=True,
            ),
            ColumnSchema(name="title", type=DatastoreDataType.TEXT, required=True),
            ColumnSchema(name="assignee", type=DatastoreDataType.USER, required=True),
        ],
        enable_rls=False,
    )
    ctx = TableContext.from_table_entity(table, "pod_test")
    record_repository = AsyncMock()
    user_repository = AsyncMock()
    user_repository.get.return_value = None
    service = RecordService(
        record_repository=record_repository,
        message_bus=AsyncMock(),
        user_repository=user_repository,
    )

    with pytest.raises(
        DatastoreValidationError,
        match="User does not exist for column 'assignee'",
    ):
        await service.update_record(
            ctx,
            str(uuid4()),
            {"assignee": str(missing_user_id)},
            uuid4(),
        )

    user_repository.get.assert_awaited_once_with(missing_user_id)
    record_repository.update_record.assert_not_called()


async def test_bulk_create_rejects_unknown_user_reference():
    missing_user_id = uuid4()
    table = DatastoreTableEntity(
        pod_id=uuid4(),
        table_name="tasks",
        primary_key_column="id",
        columns=[
            ColumnSchema(
                name="id",
                type=DatastoreDataType.UUID,
                required=True,
                unique=True,
                auto=True,
            ),
            ColumnSchema(name="title", type=DatastoreDataType.TEXT, required=True),
            ColumnSchema(name="assignee", type=DatastoreDataType.USER, required=True),
        ],
        enable_rls=False,
    )
    ctx = TableContext.from_table_entity(table, "pod_test")
    record_repository = AsyncMock()
    user_repository = AsyncMock()
    user_repository.get.return_value = None
    service = RecordService(
        record_repository=record_repository,
        message_bus=AsyncMock(),
        user_repository=user_repository,
    )

    with pytest.raises(
        DatastoreValidationError,
        match="User does not exist for column 'assignee'",
    ):
        await service.bulk_create_records(
            ctx,
            [{"title": "Review release notes", "assignee": str(missing_user_id)}],
            uuid4(),
        )

    user_repository.get.assert_awaited_once_with(missing_user_id)
    record_repository.bulk_create_records.assert_not_called()


async def test_rls_list_records_enforces_current_user_scope_for_non_admin():
    auth_context = AsyncMock()
    auth_context.can.return_value = False
    ctx = _table_context()
    user_id = uuid4()
    record_repository = AsyncMock()
    record_repository.list_records.return_value = ([], 0)
    authorization_service = AsyncMock()
    service = RecordService(
        record_repository=record_repository,
        message_bus=AsyncMock(),
        authorization_service=authorization_service,
    )

    token = set_current_context(auth_context)
    try:
        await service.list_records(ctx, user_id)
    finally:
        reset_current_context(token)

    assert record_repository.list_records.await_args.kwargs["enforce_user_scope"] is True


async def test_rls_list_records_scopes_pod_admin_by_default():
    # A pod admin (ctx.can would allow it) is still scoped to their own rows when
    # admin mode is not requested, so app apps keep per-user semantics.
    auth_context = AsyncMock()
    auth_context.can.return_value = True
    ctx = _table_context()
    user_id = uuid4()
    record_repository = AsyncMock()
    record_repository.list_records.return_value = ([], 0)
    service = RecordService(
        record_repository=record_repository,
        message_bus=AsyncMock(),
        authorization_service=AsyncMock(),
    )

    token = set_current_context(auth_context)
    try:
        await service.list_records(ctx, user_id)
    finally:
        reset_current_context(token)

    assert record_repository.list_records.await_args.kwargs["enforce_user_scope"] is True


async def test_rls_list_records_admin_mode_bypasses_scope_for_admin():
    auth_context = AsyncMock()
    auth_context.can.return_value = True  # caller administers the table
    ctx = _table_context()
    user_id = uuid4()
    record_repository = AsyncMock()
    record_repository.list_records.return_value = ([], 0)
    service = RecordService(
        record_repository=record_repository,
        message_bus=AsyncMock(),
        authorization_service=AsyncMock(),
    )

    token = set_current_context(auth_context)
    try:
        await service.list_records(ctx, user_id, admin_mode=True)
    finally:
        reset_current_context(token)

    assert record_repository.list_records.await_args.kwargs["enforce_user_scope"] is False


async def test_rls_list_records_admin_mode_rejected_for_non_admin():
    from app.modules.datastore.domain.errors import DatastoreAccessDeniedError

    auth_context = AsyncMock()
    auth_context.can.return_value = False  # caller does not administer the table
    ctx = _table_context()
    record_repository = AsyncMock()
    service = RecordService(
        record_repository=record_repository,
        message_bus=AsyncMock(),
        authorization_service=AsyncMock(),
    )

    token = set_current_context(auth_context)
    try:
        with pytest.raises(DatastoreAccessDeniedError):
            await service.list_records(ctx, uuid4(), admin_mode=True)
    finally:
        reset_current_context(token)

    record_repository.list_records.assert_not_called()


def _rls_table_entity() -> DatastoreTableEntity:
    return DatastoreTableEntity(
        pod_id=uuid4(),
        table_name="expenses",
        primary_key_column="id",
        columns=[
            ColumnSchema(
                name="id",
                type=DatastoreDataType.UUID,
                required=True,
                unique=True,
                auto=True,
            ),
            ColumnSchema(name="merchant", type=DatastoreDataType.TEXT, required=True),
            ColumnSchema(
                name="user_id",
                type=DatastoreDataType.UUID,
                required=True,
                auto=True,
                system=True,
            ),
        ],
        enable_rls=True,
    )


async def test_execute_readonly_query_scopes_to_user_by_default_even_for_admin():
    # Without admin mode, an RLS query is row-scoped to the caller even when they
    # administer the table — the admin signal is never consulted.
    ctx = AsyncMock()
    ctx.can.return_value = True  # caller administers the table
    table_service = AsyncMock()
    table_service.get_table.return_value = _rls_table_entity()
    record_repository = AsyncMock()
    record_repository.execute_readonly_query.return_value = ([], 0)
    service = RecordService(
        record_repository=record_repository,
        message_bus=AsyncMock(),
        authorization_service=AsyncMock(),
    )

    await service.execute_readonly_query(
        pod_id=uuid4(),
        query="SELECT merchant FROM expenses",
        user_id=uuid4(),
        table_service=table_service,
        ctx=ctx,
    )

    table_service.get_table.assert_awaited_once()  # per-table read authorization
    ctx.can.assert_not_awaited()
    assert record_repository.execute_readonly_query.await_args.kwargs["is_pod_admin"] is False


async def test_execute_readonly_query_admin_mode_grants_admin_rows_when_admin_on_all_rls_tables():
    ctx = AsyncMock()
    ctx.can.return_value = True  # caller administers the table
    table_service = AsyncMock()
    table_service.get_table.return_value = _rls_table_entity()
    record_repository = AsyncMock()
    record_repository.execute_readonly_query.return_value = ([{"merchant": "x"}], 1)
    service = RecordService(
        record_repository=record_repository,
        message_bus=AsyncMock(),
        authorization_service=AsyncMock(),
    )

    rows, total = await service.execute_readonly_query(
        pod_id=uuid4(),
        query="SELECT merchant FROM expenses",
        user_id=uuid4(),
        table_service=table_service,
        ctx=ctx,
        admin_mode=True,
    )

    assert (rows, total) == ([{"merchant": "x"}], 1)
    table_service.get_table.assert_awaited_once()  # per-table read authorization
    assert record_repository.execute_readonly_query.await_args.kwargs["is_pod_admin"] is True


async def test_execute_readonly_query_admin_mode_rejected_when_not_table_admin():
    from app.modules.datastore.domain.errors import DatastoreAccessDeniedError

    ctx = AsyncMock()
    ctx.can.return_value = False  # caller does not administer the table
    table_service = AsyncMock()
    table_service.get_table.return_value = _rls_table_entity()
    record_repository = AsyncMock()
    service = RecordService(
        record_repository=record_repository,
        message_bus=AsyncMock(),
        authorization_service=AsyncMock(),
    )

    with pytest.raises(DatastoreAccessDeniedError):
        await service.execute_readonly_query(
            pod_id=uuid4(),
            query="SELECT merchant FROM expenses",
            user_id=uuid4(),
            table_service=table_service,
            ctx=ctx,
            admin_mode=True,
        )

    record_repository.execute_readonly_query.assert_not_called()


async def test_execute_readonly_query_requires_pod_read_when_no_table_referenced():
    ctx = AsyncMock()
    table_service = AsyncMock()
    record_repository = AsyncMock()
    record_repository.execute_readonly_query.return_value = ([{"n": 1}], 1)
    service = RecordService(
        record_repository=record_repository,
        message_bus=AsyncMock(),
        authorization_service=AsyncMock(),
    )

    await service.execute_readonly_query(
        pod_id=uuid4(),
        query="SELECT 1",
        user_id=uuid4(),
        table_service=table_service,
        ctx=ctx,
    )

    # No registered table to authorize against -> falls back to a pod-level read check.
    ctx.require.assert_awaited()
    table_service.get_table.assert_not_awaited()
    assert record_repository.execute_readonly_query.await_args.kwargs["is_pod_admin"] is False
