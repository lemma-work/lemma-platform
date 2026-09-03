from __future__ import annotations

from unittest.mock import AsyncMock
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
    DatastoreReservedResourceError,
    DatastoreTableNotFoundError,
    DatastoreValidationError,
)
from app.modules.datastore.infrastructure.sql_identifiers import (
    MAX_IDENTIFIER_BYTES,
    ensure_identifier_fits,
)
from app.modules.datastore.services.table_service import TableService
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
    assert (
        next(column for column in arg.columns if column.name == "user_id").system
        is True
    )
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


def _durable_step_order(
    table_repository_mock: AsyncMock,
    schema_manager_mock,
) -> list[str]:
    """Record the order the two databases are committed in.

    A table lives in two of them and no transaction spans both, so the only
    thing that decides what a crash between the commits leaves behind is which
    one goes first. See the ordering rule in ``TableService``.
    """
    order: list[str] = []

    def _note(step: str):
        async def _record(*args, **kwargs):
            order.append(step)

        return _record

    table_repository_mock.commit.side_effect = _note("metadata")
    schema_manager_mock.create_table.side_effect = _note("ddl.create_table")
    schema_manager_mock.drop_table.side_effect = _note("ddl.drop_table")
    schema_manager_mock.add_column.side_effect = _note("ddl.add_column")
    return order


@pytest.mark.asyncio
async def test_create_table_makes_the_metadata_row_durable_before_the_ddl(
    table_service: TableService,
    table_repository_mock: AsyncMock,
    schema_manager_mock,
):
    """A crash between the two must leave a table the user can delete.

    The other order leaves a physical table no metadata row describes: it is
    absent from `table.list`, `table.get`/`table.delete` answer 404, and
    `table.create` fails the DDL with "already exists" and rolls its own
    metadata insert back -- so the name is unusable for good.
    """
    user_id = uuid4()
    pod_id = uuid4()
    table = _make_table(pod_id=pod_id, name="customers")
    order = _durable_step_order(table_repository_mock, schema_manager_mock)
    table_repository_mock.get_by_datastore_and_name.return_value = None
    table_repository_mock.create.return_value = table

    await _create_table_from_entity(
        table_service,
        table,
        ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
    )

    assert order == ["metadata", "ddl.create_table"], (
        "the physical table must be created after the row that describes it, "
        f"not before: {order}"
    )


@pytest.mark.asyncio
async def test_a_failed_create_does_not_leave_the_metadata_row_behind(
    table_service: TableService,
    table_repository_mock: AsyncMock,
    schema_manager_mock,
):
    """Committing first is for the crash, not for the ordinary refusal.

    An invalid column type is a 400 the caller can act on; it must not also
    leave a table in their listing that has no rows and cannot be read.
    """
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

    table_repository_mock.delete_entity.assert_awaited_once_with(table)
    assert table_repository_mock.commit.await_count == 2, (
        "the compensating delete has to be committed too, or the row it undoes "
        "is still there"
    )


@pytest.mark.asyncio
async def test_delete_table_drops_the_physical_table_before_its_metadata_row(
    table_service: TableService,
    table_repository_mock: AsyncMock,
    schema_manager_mock,
):
    """The same rule read backwards, and it is why this order is deliberate.

    Removing the row first would leave a physical table nothing describes --
    the unrecoverable direction. Dropping first leaves at worst a row whose
    table is gone, and re-issuing `table.delete` clears it.
    """
    user_id = uuid4()
    pod_id = uuid4()
    table = _make_table(pod_id=pod_id, name="customers")
    table.user_id = user_id
    order = _durable_step_order(table_repository_mock, schema_manager_mock)
    table_repository_mock.get_by_datastore_and_name.return_value = table
    table_repository_mock.delete_entity.return_value = True

    async def _note_metadata_delete(*args, **kwargs):
        order.append("metadata.delete")
        return True

    table_repository_mock.delete_entity.side_effect = _note_metadata_delete

    await table_service.delete_table(
        pod_id,
        table.table_name,
        ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
    )

    assert order.index("ddl.drop_table") < order.index("metadata.delete"), (
        f"the physical table must be dropped before its metadata row: {order}"
    )


@pytest.mark.asyncio
async def test_add_column_makes_the_metadata_change_durable_before_the_ddl(
    table_service: TableService,
    table_repository_mock: AsyncMock,
    schema_manager_mock,
):
    """Same rule: a physical column no declared schema mentions is invisible.

    `table.column.add` would then answer 409 for a column `table.get` does not
    list, forever.
    """
    pod_id = uuid4()
    user_id = uuid4()
    table = _make_table(pod_id=pod_id, name="customers")
    order = _durable_step_order(table_repository_mock, schema_manager_mock)
    table_repository_mock.get_by_datastore_and_name.return_value = table

    await table_service.add_column(
        pod_id,
        table.table_name,
        ColumnSchema(name="email", type=DatastoreDataType.TEXT),
        ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
    )

    assert order == ["metadata", "ddl.add_column"], (
        f"the column must be added to the table after it is declared: {order}"
    )


@pytest.mark.asyncio
async def test_a_failed_add_column_puts_the_declared_schema_back(
    table_service: TableService,
    table_repository_mock: AsyncMock,
    schema_manager_mock,
):
    pod_id = uuid4()
    user_id = uuid4()
    table = _make_table(pod_id=pod_id, name="customers")
    original_column_names = [column.name for column in table.columns]
    table_repository_mock.get_by_datastore_and_name.return_value = table
    schema_manager_mock.add_column.side_effect = DatastoreValidationError(
        "no such type"
    )

    with pytest.raises(DatastoreValidationError):
        await table_service.add_column(
            pod_id,
            table.table_name,
            ColumnSchema(name="email", type=DatastoreDataType.TEXT),
            ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
        )

    assert [column.name for column in table.columns] == original_column_names, (
        "the column never landed, so the declared schema must not claim it"
    )
    assert table_repository_mock.commit.await_count == 2


@pytest.mark.asyncio
async def test_a_reserved_table_name_is_refused_at_creation(
    table_service: TableService,
    table_repository_mock: AsyncMock,
    schema_manager_mock,
):
    """``reserved_`` belongs to the system, and creation was the unguarded door.

    The prefix is enforced on record writes, on listings and on ``table.get``,
    but not here -- so a user could own ``reserved_chunks``, the name the
    search service creates its chunk table under in the same pod schema. Its
    ``CREATE TABLE IF NOT EXISTS`` then no-ops against the wrong column set and
    every document upload in that pod fails, with the offending table hidden
    from ``table.list`` and unmutable through the record API.
    """
    pod_id = uuid4()
    user_id = uuid4()
    table_repository_mock.get_by_datastore_and_name.return_value = None

    with pytest.raises(DatastoreReservedResourceError, match="reserved_"):
        await table_service.create_table(
            pod_id=pod_id,
            table_name="reserved_chunks",
            primary_key_column="id",
            columns=[ColumnSchema(name="content", type=DatastoreDataType.TEXT)],
            config=None,
            enable_rls=False,
            ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
        )

    table_repository_mock.create.assert_not_awaited()
    schema_manager_mock.create_table.assert_not_awaited()


class TestALongNameIsRefusedRatherThanTruncated:
    """PostgreSQL truncates identifiers at 63 bytes *silently*, so two names
    sharing that prefix become one physical object: the second ``table.create``
    answers 409 "already exists" for a name ``table.list`` does not show, and
    the same goes for ``table.column.add``. ``record_indexes`` already defends
    its generated names with a digest; the user-chosen ones had no rule at all.
    """

    def _long(self, prefix: str) -> str:
        return prefix + "x" * (MAX_IDENTIFIER_BYTES + 1 - len(prefix))

    @pytest.mark.asyncio
    async def test_an_over_long_table_name_is_refused_before_anything_is_written(
        self,
        table_service: TableService,
        table_repository_mock: AsyncMock,
        schema_manager_mock,
    ) -> None:
        pod_id = uuid4()
        user_id = uuid4()
        table_repository_mock.get_by_datastore_and_name.return_value = None

        with pytest.raises(DatastoreValidationError, match="63 bytes"):
            await table_service.create_table(
                pod_id=pod_id,
                table_name=self._long("customer_"),
                primary_key_column="id",
                columns=[ColumnSchema(name="content", type=DatastoreDataType.TEXT)],
                config=None,
                enable_rls=False,
                ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
            )

        table_repository_mock.create.assert_not_awaited()
        schema_manager_mock.create_table.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_over_long_column_name_is_refused_too(
        self,
        table_service: TableService,
        table_repository_mock: AsyncMock,
        schema_manager_mock,
    ) -> None:
        pod_id = uuid4()
        user_id = uuid4()
        table_repository_mock.get_by_datastore_and_name.return_value = None

        with pytest.raises(DatastoreValidationError, match="63 bytes"):
            await table_service.create_table(
                pod_id=pod_id,
                table_name="customers",
                primary_key_column="id",
                columns=[
                    ColumnSchema(
                        name=self._long("annual_"), type=DatastoreDataType.TEXT
                    )
                ],
                config=None,
                enable_rls=False,
                ctx=allow_all_context(user_id=user_id, pod_id=pod_id),
            )

        table_repository_mock.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_adding_an_over_long_column_is_refused_as_well(
        self,
        table_service: TableService,
        table_repository_mock: AsyncMock,
    ) -> None:
        pod_id = uuid4()
        user_id = uuid4()

        with pytest.raises(DatastoreValidationError, match="63 bytes"):
            await table_service.add_column(
                pod_id,
                "customers",
                ColumnSchema(name=self._long("annual_"), type=DatastoreDataType.TEXT),
                allow_all_context(user_id=user_id, pod_id=pod_id),
            )

        table_repository_mock.update.assert_not_awaited()

    def test_the_limit_is_counted_in_bytes_not_characters(self) -> None:
        """PostgreSQL counts bytes, so a name of 40 accented characters is over
        the line while its length says it is not."""
        name = "é" * 40

        assert len(name) < MAX_IDENTIFIER_BYTES
        with pytest.raises(DatastoreValidationError, match="80 bytes"):
            ensure_identifier_fits(name, kind="Table name")

    def test_a_name_that_exactly_fits_is_allowed(self) -> None:
        name = "a" * MAX_IDENTIFIER_BYTES

        assert ensure_identifier_fits(name, kind="Table name") == name
