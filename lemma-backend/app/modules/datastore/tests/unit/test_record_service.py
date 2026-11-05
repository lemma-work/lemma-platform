from __future__ import annotations

import asyncio

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
from app.core.authorization.function_run import NOBODY_USER_ID
from app.modules.datastore.services.record_service import (
    RecordService as _RecordService,
)
from app.modules.datastore.services.record_validator import convert_record
from app.modules.datastore.services.table_context import TableContext


def RecordService(*, record_repository, **kwargs):
    """Build the production service for focused unit tests."""
    return _RecordService(record_repository=record_repository, **kwargs)


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


async def _every_write_capture(ctx: TableContext, user_id: UUID) -> list:
    """Insert, update, delete and bulk-insert one row each; the change captures
    the repository was handed for them."""
    record_id = str(uuid4())
    stored = type(
        "StoredRecord",
        (),
        {"user_id": None, "id": record_id, "data": {"merchant": "Hotel"}},
    )()
    captures = []

    async def write(*args, changes, **kwargs):
        captures.append(changes)
        return stored

    record_repository = AsyncMock()
    for method in ("create_record", "update_record", "delete_record"):
        getattr(record_repository, method).side_effect = write
    record_repository.bulk_create_records.side_effect = write
    service = RecordService(record_repository=record_repository)

    await service.create_record(ctx, {"merchant": "Hotel"}, user_id)
    await service.update_record(ctx, record_id, {"merchant": "Inn"}, user_id)
    await service.delete_record(ctx, record_id, user_id)
    await service.bulk_create_records(ctx, [{"merchant": "Cafe"}], user_id)
    return captures


def _working_for_contact(contact_id: UUID) -> AsyncMock:
    """A function run a contact started: the function's workload, their id."""
    ctx = AsyncMock()
    ctx.contact_id = contact_id
    return ctx


async def test_a_contacts_function_writes_rows_as_from_outside():
    """A function a contact called writes as its own workload, for nobody in
    the pod, and what it writes came from the contact's chat through a model.
    Every change it appends says so, as a visitor's form row does, so a
    DATASTORE schedule does not take it for a member's."""
    contact_id = uuid4()
    token = set_current_context(_working_for_contact(contact_id))
    try:
        captures = await _every_write_capture(_events_enabled_context(), NOBODY_USER_ID)
    finally:
        reset_current_context(token)

    assert len(captures) == 4
    for changes in captures:
        assert changes.outside_actor == f"contact:{contact_id}"


async def test_a_contacts_function_creates_rows_as_that_contact():
    """The repository is told whom the rows are for, so the contact policy
    holds the insert to them -- not to the member audience a run's empty user
    would otherwise read as."""
    contact_id = uuid4()
    run = _working_for_contact(contact_id)
    run.is_outsider = False
    handed: list[tuple[TableContext, list[dict]]] = []

    async def write(ctx, rows, user_id, **kwargs):
        handed.append((ctx, rows if isinstance(rows, list) else [rows]))
        return type("StoredRecord", (), {"data": {}})()

    record_repository = AsyncMock()
    record_repository.create_record.side_effect = write
    record_repository.bulk_create_records.side_effect = write
    service = RecordService(
        record_repository=record_repository, authorization_service=object()
    )
    table = _events_enabled_context()
    table.contact_owned = True
    token = set_current_context(run)
    try:
        await service.create_record(table, {"merchant": "Hotel"}, NOBODY_USER_ID)
        await service.bulk_create_records(table, [{"merchant": "Inn"}], NOBODY_USER_ID)
    finally:
        reset_current_context(token)

    assert table.for_contact is None
    assert [ctx.for_contact for ctx, _rows in handed] == [contact_id, contact_id]
    for _ctx, rows in handed:
        assert [row["contact_id"] for row in rows] == [str(contact_id)]


async def test_a_members_write_and_a_visitors_row_keep_their_own_names():
    user_id = uuid4()
    token = set_current_context(_member_ctx())
    try:
        member = await _every_write_capture(_events_enabled_context(), user_id)
        opened = _events_enabled_context()
        opened.outside_actor = "visitor:session-1"
        visitor = await _every_write_capture(opened, user_id)
    finally:
        reset_current_context(token)

    assert {changes.outside_actor for changes in member} == {None}
    assert {changes.outside_actor for changes in visitor} == {"visitor:session-1"}


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
        {
            "user_id": uuid4(),
            "id": str(uuid4()),
            "data": {"merchant": "Cafe", "user_id": str(user_id)},
        },
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


async def test_a_record_delete_asks_for_record_delete_not_write():
    """H14: deleting a row asked for ``record.write``, so a writer emptied tables."""
    auth_context = AsyncMock()
    ctx = _table_context()
    ctx.enable_rls = False
    service = RecordService(
        record_repository=AsyncMock(), authorization_service=AsyncMock()
    )
    service.change_log.after_commit = AsyncMock()

    token = set_current_context(auth_context)
    try:
        await service.delete_record(ctx, "row-1", uuid4())
    finally:
        reset_current_context(token)

    asked = [call.args[0] for call in auth_context.require.await_args_list]
    assert asked == [Permissions.DATASTORE_RECORD_DELETE]


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
    user_repository.existing_ids.return_value = set()
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

    user_repository.existing_ids.assert_awaited_once_with({missing_user_id})
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
    user_repository.existing_ids.return_value = set()
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

    user_repository.existing_ids.assert_awaited_once_with({missing_user_id})
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
    user_repository.existing_ids.return_value = set()
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

    user_repository.existing_ids.assert_awaited_once_with({missing_user_id})
    record_repository.bulk_create_records.assert_not_called()


def _user_table_context():
    """A table whose USER column is what the reference check reads."""
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
    return TableContext.from_table_entity(table, "pod_test")


async def test_bulk_create_checks_every_named_user_in_one_read():
    """The cost of validating a batch is one read, whoever it names.

    The regression this pins is not "too many queries" in the abstract: the
    dedup set that used to sit here made a batch naming ONE person cheap and
    left a batch naming a different person per row at one round trip per row,
    which is the shape an import actually has. Both are asserted, because
    fixing only the first is what the sibling already did.
    """
    ctx = _user_table_context()
    one_owner = uuid4()
    many_owners = [uuid4() for _ in range(50)]
    record_repository = AsyncMock()
    record_repository.bulk_create_records.return_value = 50

    for assignees in ([one_owner] * 50, many_owners):
        user_repository = AsyncMock()
        user_repository.existing_ids.return_value = set(assignees)
        service = RecordService(
            record_repository=record_repository,
            user_repository=user_repository,
        )

        await service.bulk_create_records(
            ctx,
            [
                {"title": f"task {index}", "assignee": str(assignee)}
                for index, assignee in enumerate(assignees)
            ],
            uuid4(),
        )

        assert user_repository.existing_ids.await_count == 1
        user_repository.existing_ids.assert_awaited_once_with(set(assignees))


async def test_bulk_create_names_the_first_offending_column_in_input_order():
    """Which row is reported matters -- it is how someone finds it in their file."""
    ctx = _user_table_context()
    known = uuid4()
    missing = uuid4()
    user_repository = AsyncMock()
    user_repository.existing_ids.return_value = {known}
    record_repository = AsyncMock()
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
            [
                {"title": "first", "assignee": str(known)},
                {"title": "second", "assignee": str(missing)},
                {"title": "third", "assignee": str(known)},
            ],
            uuid4(),
        )

    record_repository.bulk_create_records.assert_not_called()


async def test_a_deactivated_user_is_still_a_valid_reference():
    """`existing_ids` asks who exists, not who can sign in.

    A USER column records who a row belongs to. Someone leaving the company
    must not make every row naming them unwritable, which is why the batch
    lookup is as permissive as the per-id `get` it replaced -- the repository
    method carries the same note.
    """
    ctx = _user_table_context()
    departed = uuid4()
    user_repository = AsyncMock()
    user_repository.existing_ids.return_value = {departed}
    record_repository = AsyncMock()
    record_repository.bulk_create_records.return_value = 1
    service = RecordService(
        record_repository=record_repository,
        user_repository=user_repository,
    )

    written = await service.bulk_create_records(
        ctx, [{"title": "handover", "assignee": str(departed)}], uuid4()
    )

    assert written == 1


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

    assert (
        record_repository.list_records.await_args.kwargs["enforce_user_scope"] is True
    )


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

    assert (
        record_repository.list_records.await_args.kwargs["enforce_user_scope"] is True
    )


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

    assert (
        record_repository.list_records.await_args.kwargs["enforce_user_scope"] is False
    )


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


def _member_ctx() -> AsyncMock:
    """A member's context: no contact on it, and not an outsider."""
    ctx = AsyncMock()
    ctx.contact_id = None
    ctx.is_outsider = False
    return ctx


async def test_execute_readonly_query_scopes_to_user_by_default_even_for_admin():
    # Without admin mode, an RLS query is row-scoped to the caller even when they
    # administer the table — the admin signal is never consulted.
    ctx = _member_ctx()
    ctx.can.return_value = True  # caller administers the table
    table_service = AsyncMock()
    table_service.get_tables.return_value = {"expenses": _rls_table_entity()}
    record_repository = AsyncMock()
    record_repository.execute_readonly_query.return_value = ([], 0, False)
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

    table_service.get_tables.assert_awaited_once()  # per-table read authorization
    ctx.can.assert_not_awaited()
    assert (
        record_repository.execute_readonly_query.await_args.kwargs[
            "principal"
        ].is_pod_admin
        is False
    )


async def test_execute_readonly_query_admin_mode_grants_admin_rows_when_admin_on_all_rls_tables():
    ctx = _member_ctx()
    ctx.can.return_value = True  # caller administers the table
    table_service = AsyncMock()
    table_service.get_tables.return_value = {"expenses": _rls_table_entity()}
    record_repository = AsyncMock()
    record_repository.execute_readonly_query.return_value = (
        [{"merchant": "x"}],
        1,
        False,
    )
    service = RecordService(
        record_repository=record_repository,
        authorization_service=AsyncMock(),
    )

    rows, total, truncated = await service.execute_readonly_query(
        pod_id=uuid4(),
        query="SELECT merchant FROM expenses",
        user_id=uuid4(),
        table_service=table_service,
        ctx=ctx,
        admin_mode=True,
    )

    assert (rows, total) == ([{"merchant": "x"}], 1)
    table_service.get_tables.assert_awaited_once()  # per-table read authorization
    assert (
        record_repository.execute_readonly_query.await_args.kwargs[
            "principal"
        ].is_pod_admin
        is True
    )


async def test_execute_readonly_query_admin_mode_rejected_when_not_table_admin():
    from app.modules.datastore.domain.errors import DatastoreAccessDeniedError

    ctx = _member_ctx()
    ctx.can.return_value = False  # caller does not administer the table
    table_service = AsyncMock()
    table_service.get_tables.return_value = {"expenses": _rls_table_entity()}
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
    ctx = _member_ctx()
    table_service = AsyncMock()
    record_repository = AsyncMock()
    record_repository.execute_readonly_query.return_value = ([{"n": 1}], 1, False)
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
    assert (
        record_repository.execute_readonly_query.await_args.kwargs[
            "principal"
        ].is_pod_admin
        is False
    )


async def test_bulk_update_checks_permission_and_wakes_once(monkeypatch):
    """Bulk work must cost one authorization check, not one per record.

    `bulk_update_records` looped over `update_record`, which re-runs the whole
    single-record preamble every time: a DATASTORE_RECORD_WRITE check against
    the database and a connection release, a row-scope decision, and an event
    dispatch. All of them are invariant across the loop -- same caller, same
    table, same mode -- so a 100-record update paid for 100 of each.

    `bulk_create_records` in the same service already hoists its permission
    check and stages events for one dispatch. This is that shape, applied to
    update, and the difference showed up as `records/bulk/update` latency that
    scaled with the batch instead of staying flat.

    The rows themselves are now written by one repository call in one
    transaction, rather than N calls that each opened a session, set the RLS
    context, staged an event and committed.
    """
    ctx = _events_enabled_context()
    user_id = uuid4()
    record_repository = AsyncMock()
    batched: list[tuple] = []

    async def _fake_bulk_update(repository, ctx_arg, updates_arg, user_arg, **kwargs):
        batched.append((repository, updates_arg))
        return len(updates_arg)

    monkeypatch.setattr(
        "app.modules.datastore.services.record_service.write_bulk_updates",
        _fake_bulk_update,
    )

    service = RecordService(record_repository=record_repository)
    authz = AsyncMock()
    authz.should_enforce_record_user_scope.return_value = False
    service.authz = authz
    service.change_log.after_commit = AsyncMock()

    updates = [{"id": str(uuid4()), "status": "done"} for _ in range(5)]
    count = await service.bulk_update_records(ctx, updates, user_id)

    assert count == 5
    assert len(batched) == 1, (
        "five rows must be one transaction, not five -- update_record opened a "
        "session, set RLS, staged an event and committed for every row"
    )
    assert record_repository.update_record.await_count == 0, (
        "the per-row path must not be reachable from a bulk call"
    )
    assert len(batched[0][1]) == 5, "every row still reaches the writer"
    assert authz.require_record_write.await_count == 1, (
        f"{authz.require_record_write.await_count} permission checks for one bulk "
        "call; the caller and table do not change inside the loop"
    )
    assert authz.should_enforce_record_user_scope.await_count == 1, (
        f"{authz.should_enforce_record_user_scope.await_count} scope decisions "
        "for one bulk call; the mode does not change inside the loop"
    )
    assert service.change_log.after_commit.await_count == 1, (
        f"{service.change_log.after_commit.await_count} wakes for one bulk call"
    )


async def test_bulk_delete_checks_permission_once_and_still_wakes(
    monkeypatch,
):
    """Same preamble hoist as bulk update, and the events must still be flushed.

    The batch writer stages a DELETE event per row and deliberately does not
    dispatch, so the batch has to flush once at the end. Getting that wrong
    would publish nothing at all, which is worse than the slowness this fixes.
    """
    ctx = _events_enabled_context()
    user_id = uuid4()
    record_repository = AsyncMock()
    batched: list[list] = []

    async def _fake_bulk_delete(repository, ctx_arg, ids_arg, user_arg, **kwargs):
        batched.append(list(ids_arg))
        return len(ids_arg)

    monkeypatch.setattr(
        "app.modules.datastore.services.record_service.write_bulk_deletes",
        _fake_bulk_delete,
    )

    service = RecordService(record_repository=record_repository)
    authz = AsyncMock()
    authz.should_enforce_record_user_scope.return_value = False
    service.authz = authz
    service.change_log.after_commit = AsyncMock()

    count = await service.bulk_delete_records(ctx, [uuid4() for _ in range(4)], user_id)

    assert count == 4
    assert len(batched) == 1, (
        "four rows must be one transaction, not four -- delete_record opened a "
        "session, set RLS, staged an event and committed for every row, so a "
        "batch that failed halfway left the first half destroyed"
    )
    assert record_repository.delete_record.await_count == 0, (
        "the per-row path must not be reachable from a bulk call"
    )
    assert authz.require_record_delete.await_count == 1
    assert authz.require_record_write.await_count == 0, "a delete is not a write"
    assert authz.should_enforce_record_user_scope.await_count == 1
    assert service.change_log.after_commit.await_count == 1, (
        "the batch's changes never woke the live hubs"
    )


async def test_a_single_update_still_checks_permission_and_wakes():
    """The split must not weaken the single-record path it was extracted from."""
    ctx = _events_enabled_context()
    user_id = uuid4()
    record_repository = AsyncMock()
    record_repository.update_record.return_value = {"id": "x"}
    service = RecordService(record_repository=record_repository)
    authz = AsyncMock()
    authz.should_enforce_record_user_scope.return_value = True
    service.authz = authz
    service.change_log.after_commit = AsyncMock()

    await service.update_record(ctx, "row-1", {"status": "done"}, user_id)

    assert authz.require_record_write.await_count == 1
    assert service.change_log.after_commit.await_count == 1
    # The scope decision still reaches the repository.
    assert (
        record_repository.update_record.await_args.kwargs["enforce_user_scope"] is True
    )


@pytest.mark.asyncio
async def test_the_wake_after_a_write_never_holds_the_platform_connection():
    """Records live in a different database from the request's unit of work.

    The wake after a write is a Redis round trip, and the request still holds
    the *platform* connection its authorization reads checked out. The wake is
    started in the background, so ``after_commit`` returns without waiting on
    Redis and without touching that connection at all.
    """
    from app.modules.datastore.domain.write_intent import WriteIntent
    from app.modules.datastore.services.record_change_log import RecordChangeLog

    change_log = RecordChangeLog()
    changes = await change_log.capture(_events_enabled_context(), WriteIntent.NORMAL)
    # Bounded well below the publish's own timeout: nothing here waits on Redis.
    await asyncio.wait_for(change_log.after_commit(changes), timeout=0.05)
