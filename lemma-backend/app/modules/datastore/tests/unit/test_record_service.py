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
    DatastoreRecordEvent,
    DatastoreRecordOperation,
)
from app.modules.datastore.services.record_service import RecordService as _RecordService
from app.modules.datastore.services.record_validator import convert_record
from app.modules.datastore.services.table_context import TableContext


def RecordService(
    *,
    record_repository,
    event_dispatcher=None,
    **kwargs,
):
    """Build the production transactional-only service for focused unit tests."""
    return _RecordService(
        record_repository=record_repository,
        event_dispatcher=event_dispatcher or AsyncMock(),
        **kwargs,
    )


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


async def test_create_record_stages_record_event_for_outbox():
    ctx = _events_enabled_context()
    user_id = uuid4()
    record_id = str(uuid4())
    record_repository = AsyncMock()
    stored = type(
        "StoredRecord",
        (),
        {"user_id": uuid4(), "id": record_id, "data": {"merchant": "Hotel"}},
    )()
    staged_events = []

    async def create_record(_ctx, _data, _user_id, *, event_factory):
        staged_events.append(event_factory(stored))
        return stored

    record_repository.create_record.side_effect = create_record
    dispatcher = AsyncMock()
    service = RecordService(
        record_repository=record_repository,
        event_dispatcher=dispatcher,
    )

    await service.create_record(ctx, {"merchant": "Hotel"}, user_id)

    event = staged_events[0]
    assert isinstance(event, DatastoreRecordEvent)
    assert event.event_type == "datastore.record.insert"
    assert event.operation == DatastoreRecordOperation.INSERT
    assert event.actor_id == user_id
    assert event.table_name == "expenses"
    assert event.record_id == record_id
    dispatcher.assert_awaited_once()


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
    dispatcher = AsyncMock()
    service = RecordService(
        record_repository=record_repository,
        event_dispatcher=dispatcher,
    )

    await service.create_record(ctx, {"merchant": "Hotel"}, user_id)

    dispatcher.assert_awaited_once()
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
    dispatcher = AsyncMock()
    service = RecordService(
        record_repository=record_repository,
        event_dispatcher=dispatcher,
    )

    await service.create_record(ctx, {"merchant": "Hotel"}, uuid4())

    assert record_repository.create_record.await_args.kwargs["event_factory"] is None
    dispatcher.assert_not_awaited()


async def test_update_and_delete_emit_record_events():
    ctx = _events_enabled_context()
    user_id = uuid4()
    record_id = str(uuid4())
    record_repository = AsyncMock()
    updated = type(
        "StoredRecord",
        (),
        {"user_id": uuid4(), "id": record_id, "data": {"merchant": "Retreat"}},
    )()
    deleted = type(
        "DeletedRecord",
        (),
        {"user_id": user_id, "id": record_id, "data": {"merchant": "Retreat"}},
    )()
    staged_events = []

    async def update_record(*args, event_factory, **kwargs):
        staged_events.append(event_factory(updated))
        return updated

    async def delete_record(*args, event_factory, **kwargs):
        staged_events.append(event_factory(deleted))
        return deleted

    record_repository.update_record.side_effect = update_record
    record_repository.delete_record.side_effect = delete_record
    service = RecordService(record_repository=record_repository)

    await service.update_record(ctx, record_id, {"merchant": "Retreat"}, user_id)
    update_event = staged_events[-1]
    assert update_event.operation == DatastoreRecordOperation.UPDATE
    assert update_event.event_type == "datastore.record.update"
    assert update_event.actor_id == user_id

    await service.delete_record(ctx, record_id, user_id)
    delete_event = staged_events[-1]
    assert delete_event.operation == DatastoreRecordOperation.DELETE
    assert delete_event.event_type == "datastore.record.delete"


async def test_event_payload_is_the_stored_row_not_what_was_submitted():
    """A subscriber must see columns the writer never mentioned.

    The submitted dict carries only what the caller typed; the stored row also
    carries the id, the timestamps, and anything the database defaulted. A
    match condition on a defaulted column is only answerable from the latter.
    """
    ctx = _events_enabled_context()
    record_id = str(uuid4())
    stored_row = {
        "id": record_id,
        "merchant": "Hotel",
        "status": "new",  # a default the caller never submitted
        "created_at": "2026-08-06T00:00:00Z",
    }
    stored = type(
        "StoredRecord", (), {"user_id": uuid4(), "id": record_id, "data": stored_row}
    )()
    staged_events = []
    record_repository = AsyncMock()

    async def create_record(_ctx, _data, _user_id, *, event_factory):
        staged_events.append(event_factory(stored))
        return stored

    record_repository.create_record.side_effect = create_record
    service = RecordService(record_repository=record_repository)

    await service.create_record(ctx, {"merchant": "Hotel"}, uuid4())

    assert staged_events[0].payload == stored_row
    assert staged_events[0].changed is None
    assert staged_events[0].previous is None


async def test_update_event_carries_what_changed_and_what_it_was():
    ctx = _events_enabled_context()
    record_id = str(uuid4())
    updated = type(
        "StoredRecord",
        (),
        {
            "user_id": uuid4(),
            "id": record_id,
            "data": {"id": record_id, "merchant": "Retreat", "status": "approved"},
        },
    )()
    staged_events = []
    record_repository = AsyncMock()

    async def update_record(*args, event_factory, **kwargs):
        # Stands in for the repository, which is the only layer that knows
        # which columns the statement wrote and what they held before.
        staged_events.append(
            event_factory(updated, ["status"], {"status": "pending"})
        )
        return updated

    record_repository.update_record.side_effect = update_record
    service = RecordService(record_repository=record_repository)

    await service.update_record(ctx, record_id, {"status": "approved"}, uuid4())

    event = staged_events[-1]
    assert event.payload["merchant"] == "Retreat"  # untouched column still present
    assert event.changed == ["status"]
    assert event.previous == {"status": "pending"}


async def test_delete_event_carries_the_row_that_was_removed():
    """It used to carry `{}`, which no condition could ever match."""
    ctx = _events_enabled_context()
    record_id = str(uuid4())
    removed_row = {"id": record_id, "merchant": "Retreat", "status": "archived"}
    deleted = type(
        "DeletedRecord", (), {"user_id": uuid4(), "id": record_id, "data": removed_row}
    )()
    staged_events = []
    record_repository = AsyncMock()

    async def delete_record(*args, event_factory, **kwargs):
        staged_events.append(event_factory(deleted))
        return deleted

    record_repository.delete_record.side_effect = delete_record
    service = RecordService(record_repository=record_repository)

    await service.delete_record(ctx, record_id, uuid4())

    assert staged_events[-1].payload == removed_row


async def test_bulk_create_emits_one_insert_event_per_written_row():
    """Bulk events are built from the rows written, like every other write.

    The service used to build them from the submitted data and guess each
    record id from it, so a bulk-inserted row carried neither its generated id
    nor anything the database defaulted — and a condition on such a column
    silently never matched.
    """
    ctx = _events_enabled_context()
    user_id = uuid4()
    record_repository = AsyncMock()
    written_rows = [
        {"id": str(uuid4()), "merchant": "Hotel", "status": "new"},
        {"id": str(uuid4()), "merchant": "Cafe", "status": "new"},
    ]
    staged_events = []

    async def bulk_create_records(_ctx, _records, _user_id, *, event_factory):
        for row in written_rows:
            stored = type(
                "StoredRecord", (), {"user_id": uuid4(), "id": row["id"], "data": row}
            )()
            staged_events.append(event_factory(stored))
        return len(written_rows)

    record_repository.bulk_create_records.side_effect = bulk_create_records
    service = RecordService(record_repository=record_repository)

    await service.bulk_create_records(
        ctx,
        [{"merchant": "Hotel"}, {"merchant": "Cafe"}],
        user_id,
    )

    assert len(staged_events) == 2
    for event, row in zip(staged_events, written_rows):
        assert event.operation == DatastoreRecordOperation.INSERT
        assert event.actor_id == user_id
        assert event.record_id == row["id"]
        assert event.payload == row  # the stored row, defaults and all


async def test_bulk_create_passes_no_event_factory_when_events_disabled():
    ctx = _table_context()  # events_enabled defaults to False
    record_repository = AsyncMock()
    record_repository.bulk_create_records.return_value = 1
    service = RecordService(record_repository=record_repository)

    await service.bulk_create_records(ctx, [{"merchant": "Hotel"}], uuid4())

    kwargs = record_repository.bulk_create_records.await_args.kwargs
    assert kwargs["event_factory"] is None


async def test_record_event_carries_row_owner_for_rls_table():
    """RLS tables tag events with the row owner so change subscribers can scope
    delivery to that user without a database read."""
    ctx = _events_enabled_rls_context()
    caller = uuid4()
    owner = uuid4()
    record_id = str(uuid4())
    record_repository = AsyncMock()
    stored = type(
        "StoredRecord",
        (),
        {"user_id": owner, "id": record_id, "data": {"merchant": "Hotel"}},
    )()
    staged_events = []

    async def create_record(*args, event_factory, **kwargs):
        staged_events.append(event_factory(stored))
        return stored

    record_repository.create_record.side_effect = create_record
    service = RecordService(record_repository=record_repository)

    await service.create_record(ctx, {"merchant": "Hotel"}, caller)

    event = staged_events[0]
    assert event.owner_user_id == owner
    assert event.actor_id == caller


async def test_record_event_omits_owner_for_non_rls_table():
    """Shared (non-RLS) rows carry no owner, so subscribers fan them out to every
    member who can read the table."""
    ctx = _events_enabled_context()  # enable_rls=False
    record_repository = AsyncMock()
    stored = type(
        "StoredRecord",
        (),
        {"user_id": uuid4(), "id": str(uuid4()), "data": {"merchant": "Hotel"}},
    )()
    staged_events = []

    async def create_record(*args, event_factory, **kwargs):
        staged_events.append(event_factory(stored))
        return stored

    record_repository.create_record.side_effect = create_record
    service = RecordService(record_repository=record_repository)

    await service.create_record(ctx, {"merchant": "Hotel"}, uuid4())

    event = staged_events[0]
    assert event.owner_user_id is None


async def test_delete_record_event_owner_defaults_to_caller_on_rls_table():
    """A self-scoped RLS delete owns its own row, so the event is tagged to the
    deleting user."""
    ctx = _events_enabled_rls_context()
    caller = uuid4()
    record_id = str(uuid4())
    record_repository = AsyncMock()
    deleted = type(
        "DeletedRecord",
        (),
        {"user_id": caller, "id": record_id, "data": {}},
    )()
    staged_events = []

    async def delete_record(*args, event_factory, **kwargs):
        staged_events.append(event_factory(deleted))
        return deleted

    record_repository.delete_record.side_effect = delete_record
    service = RecordService(record_repository=record_repository)

    await service.delete_record(ctx, record_id, caller)

    event = staged_events[0]
    assert event.operation == DatastoreRecordOperation.DELETE
    assert event.owner_user_id == caller


async def test_transactional_admin_delete_stages_original_rls_row_owner():
    ctx = _events_enabled_rls_context()
    actor = uuid4()
    row_owner = uuid4()
    record_id = str(uuid4())
    deleted = type(
        "DeletedRecord",
        (),
        {"user_id": row_owner, "id": record_id, "data": {}},
    )()
    staged_events = []
    record_repository = AsyncMock()

    async def delete_record(
        _ctx,
        _record_id,
        _user_id,
        *,
        enforce_user_scope,
        event_factory,
    ):
        assert enforce_user_scope is False
        staged_events.append(event_factory(deleted))
        return deleted

    record_repository.delete_record.side_effect = delete_record
    dispatcher = AsyncMock()
    auth_context = AsyncMock()
    auth_context.can.return_value = True
    service = RecordService(
        record_repository=record_repository,
        event_dispatcher=dispatcher,
        authorization_service=object(),
    )

    token = set_current_context(auth_context)
    try:
        await service.delete_record(ctx, record_id, actor, admin_mode=True)
    finally:
        reset_current_context(token)

    dispatcher.assert_awaited_once()
    assert len(staged_events) == 1
    assert staged_events[0].actor_id == actor
    assert staged_events[0].owner_user_id == row_owner


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
    service = RecordService(record_repository=record_repository)

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
    service = RecordService(record_repository=record_repository)

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


async def test_bulk_update_checks_permission_and_dispatches_events_once():
    """Bulk work must cost one authorization check, not one per record.

    `bulk_update_records` looped over `update_record`, which re-runs the whole
    single-record preamble every time: a DATASTORE_RECORD_WRITE check against
    the database and a connection release, a row-scope decision, and an event
    dispatch. All of them are invariant across the loop -- same caller, same
    table, same mode -- so a 100-record update paid for 100 of each.

    `bulk_create_records` in the same service already hoists its permission
    check and stages events for one dispatch. This is that shape, applied to
    update. Production saw the difference as p50 8.6s and max 14.9s on
    `records/bulk/update`.
    """
    ctx = _events_enabled_context()
    user_id = uuid4()
    record_repository = AsyncMock()
    record_repository.update_record.return_value = {"id": "x"}

    service = RecordService(record_repository=record_repository)
    authz = AsyncMock()
    authz.should_enforce_record_user_scope.return_value = False
    service.authz = authz
    service.events = AsyncMock()

    updates = [{"id": str(uuid4()), "status": "done"} for _ in range(5)]
    count = await service.bulk_update_records(ctx, updates, user_id)

    assert count == 5
    assert record_repository.update_record.await_count == 5, "every row is written"
    assert authz.require_record_write.await_count == 1, (
        f"{authz.require_record_write.await_count} permission checks for one bulk "
        "call; the caller and table do not change inside the loop"
    )
    assert authz.should_enforce_record_user_scope.await_count == 1, (
        f"{authz.should_enforce_record_user_scope.await_count} scope decisions "
        "for one bulk call; the mode does not change inside the loop"
    )
    assert service.events.dispatch.await_count == 1, (
        f"{service.events.dispatch.await_count} event dispatches for one bulk call"
    )


async def test_bulk_delete_checks_permission_once_and_still_publishes_events():
    """Same preamble hoist as bulk update, and the events must still be flushed.

    `_write_delete` stages a DELETE event per row but deliberately does not
    dispatch, so the batch has to flush once at the end. Getting that wrong
    would publish nothing at all, which is worse than the slowness this fixes.
    """
    ctx = _events_enabled_context()
    user_id = uuid4()
    record_repository = AsyncMock()
    service = RecordService(record_repository=record_repository)
    authz = AsyncMock()
    authz.should_enforce_record_user_scope.return_value = False
    service.authz = authz
    service.events = AsyncMock()

    count = await service.bulk_delete_records(ctx, [uuid4() for _ in range(4)], user_id)

    assert count == 4
    assert record_repository.delete_record.await_count == 4
    assert authz.require_record_write.await_count == 1
    assert authz.should_enforce_record_user_scope.await_count == 1
    assert service.events.dispatch.await_count == 1, (
        "staged DELETE events were never flushed"
    )


async def test_a_single_update_still_checks_permission_and_dispatches():
    """The split must not weaken the single-record path it was extracted from."""
    ctx = _events_enabled_context()
    user_id = uuid4()
    record_repository = AsyncMock()
    record_repository.update_record.return_value = {"id": "x"}
    service = RecordService(record_repository=record_repository)
    authz = AsyncMock()
    authz.should_enforce_record_user_scope.return_value = True
    service.authz = authz
    service.events = AsyncMock()

    await service.update_record(ctx, "row-1", {"status": "done"}, user_id)

    assert authz.require_record_write.await_count == 1
    assert service.events.dispatch.await_count == 1
    # The scope decision still reaches the repository.
    assert record_repository.update_record.await_args.kwargs["enforce_user_scope"] is True
