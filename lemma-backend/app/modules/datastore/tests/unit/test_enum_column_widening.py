"""`table.column.update`: an ENUM column takes more options, never fewer.

The physical CHECK is exercised against PostgreSQL in
``e2e/test_enum_column_widening_e2e.py``; these pin the decision and the
order the two databases are written in.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError

from app.core.domain.errors import DomainError
from app.modules.datastore.api.schemas.table_schemas import UpdateColumnRequest
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    DatastoreTableEntity,
)
from app.modules.datastore.domain.column_changes import (
    ColumnChange,
    column_change,
    widened_enum_column,
)
from app.modules.datastore.domain.errors import (
    DatastoreColumnChangeRefusedError,
    DatastoreTableNotFoundError,
)
from app.modules.datastore.infrastructure.schema_manager import SchemaManager
from app.modules.datastore.services.table_service import TableService
from app.modules.test_support.authz import allow_all_context, deny_all_context


def _tickets() -> DatastoreTableEntity:
    return DatastoreTableEntity(
        pod_id=uuid4(),
        table_name="tickets",
        primary_key_column="id",
        columns=[
            ColumnSchema(name="title", type=DatastoreDataType.TEXT),
            ColumnSchema(
                name="status",
                type=DatastoreDataType.ENUM,
                options=["open", "closed"],
                required=True,
                default="open",
                description="Where the ticket is",
            ),
        ],
        enable_rls=False,
    )


def _status(table: DatastoreTableEntity) -> ColumnSchema:
    return next(column for column in table.columns if column.name == "status")


class TestWhatAColumnMayBecome:
    def test_new_options_are_added_after_the_current_ones(self):
        widened = widened_enum_column(
            _tickets(), "status", ["open", "closed", "waiting"]
        )

        assert widened is not None
        assert widened.options == ["open", "closed", "waiting"]

    def test_everything_but_the_options_is_kept(self):
        widened = widened_enum_column(_tickets(), "status", ["open", "closed", "x"])

        assert widened is not None
        assert (widened.required, widened.default, widened.description) == (
            True,
            "open",
            "Where the ticket is",
        )

    def test_the_current_options_change_nothing(self):
        assert widened_enum_column(_tickets(), "status", ["open", "closed"]) is None

    def test_a_repeated_option_counts_once(self):
        assert (
            widened_enum_column(_tickets(), "status", ["open", "closed", "open"])
            is None
        )

    def test_dropping_an_option_is_refused_and_names_it(self):
        with pytest.raises(DatastoreColumnChangeRefusedError) as refused:
            widened_enum_column(_tickets(), "status", ["open", "waiting"])

        assert refused.value.status_code == 422
        assert "'closed'" in str(refused.value)
        assert refused.value.details == {
            "field": "status",
            "missing_options": ["closed"],
        }

    def test_a_column_that_is_not_enum_is_refused(self):
        with pytest.raises(DatastoreColumnChangeRefusedError) as refused:
            widened_enum_column(_tickets(), "title", ["a", "b"])

        assert refused.value.status_code == 422
        assert "TEXT, not ENUM" in str(refused.value)

    def test_a_column_the_table_does_not_declare_is_not_found(self):
        with pytest.raises(DatastoreTableNotFoundError):
            widened_enum_column(_tickets(), "priority", ["low"])


class TestTheRequest:
    def test_options_are_required_and_not_empty(self):
        with pytest.raises(ValidationError):
            UpdateColumnRequest.model_validate({})
        with pytest.raises(ValidationError):
            UpdateColumnRequest.model_validate({"options": []})

    def test_a_field_it_does_not_change_is_refused_rather_than_ignored(self):
        with pytest.raises(ValidationError):
            UpdateColumnRequest.model_validate({"options": ["open"], "type": "TEXT"})


@pytest.mark.asyncio
class TestTableServiceUpdateColumn:
    async def test_widening_changes_the_check_then_the_declared_schema(
        self,
        table_service: TableService,
        table_repository_mock: AsyncMock,
        schema_manager_mock: MagicMock,
    ):
        """The CHECK first: interrupted between the two, it accepts an option the
        record API still refuses, and sending the same options again finishes
        the job. The other order leaves options listed that the database
        refuses, and a retry sees them listed and does nothing.
        """
        table = _tickets()
        table_repository_mock.get_by_datastore_and_name.return_value = table
        order: list[str] = []

        async def _ddl(*args, **kwargs):
            order.append("ddl")

        async def _metadata(entity):
            order.append("metadata")
            return entity

        schema_manager_mock.change_column_constraints.side_effect = _ddl
        table_repository_mock.update.side_effect = _metadata
        user_id = uuid4()

        await table_service.update_column(
            table.pod_id,
            "tickets",
            "status",
            ["open", "closed", "waiting"],
            ctx=allow_all_context(user_id=user_id, pod_id=table.pod_id),
        )

        assert order == ["ddl", "metadata"]
        pod_id, table_name, change = (
            schema_manager_mock.change_column_constraints.await_args.args
        )
        column = change.column
        assert (pod_id, table_name, column.name) == (table.pod_id, "tickets", "status")
        assert column.options == ["open", "closed", "waiting"]
        assert change.options_widened and not change.foreign_key_dropped
        assert _status(table).options == ["open", "closed", "waiting"]
        events = table.collect_events()
        assert [event.event_type for event in events] == [
            "datastore.table.column.updated"
        ]
        assert events[0].column_name == "status"
        assert events[0].actor_id == user_id

    async def test_the_current_options_touch_neither_database(
        self,
        table_service: TableService,
        table_repository_mock: AsyncMock,
        schema_manager_mock: MagicMock,
    ):
        table = _tickets()
        table_repository_mock.get_by_datastore_and_name.return_value = table

        result = await table_service.update_column(
            table.pod_id,
            "tickets",
            "status",
            ["open", "closed"],
            ctx=allow_all_context(user_id=uuid4(), pod_id=table.pod_id),
        )

        assert result is table
        schema_manager_mock.change_column_constraints.assert_not_awaited()
        table_repository_mock.update.assert_not_awaited()

    @pytest.mark.parametrize(
        ("column_name", "options"),
        [("status", ["open"]), ("title", ["a"])],
        ids=["narrowing", "not-enum"],
    )
    async def test_a_refused_change_touches_neither_database(
        self,
        table_service: TableService,
        table_repository_mock: AsyncMock,
        schema_manager_mock: MagicMock,
        column_name: str,
        options: list[str],
    ):
        table = _tickets()
        table_repository_mock.get_by_datastore_and_name.return_value = table

        with pytest.raises(DatastoreColumnChangeRefusedError):
            await table_service.update_column(
                table.pod_id,
                "tickets",
                column_name,
                options,
                ctx=allow_all_context(user_id=uuid4(), pod_id=table.pod_id),
            )

        schema_manager_mock.change_column_constraints.assert_not_awaited()
        table_repository_mock.update.assert_not_awaited()
        assert _status(table).options == ["open", "closed"]

    async def test_it_needs_the_table_update_permission(
        self,
        table_service: TableService,
        table_repository_mock: AsyncMock,
        schema_manager_mock: MagicMock,
    ):
        table = _tickets()
        table_repository_mock.get_by_datastore_and_name.return_value = table

        with pytest.raises(DomainError) as refused:
            await table_service.update_column(
                table.pod_id,
                "tickets",
                "status",
                ["open", "closed", "waiting"],
                ctx=deny_all_context(user_id=uuid4(), pod_id=table.pod_id),
            )

        assert refused.value.status_code == 403
        schema_manager_mock.change_column_constraints.assert_not_awaited()

    async def test_a_failed_check_leaves_the_declared_schema_alone(
        self,
        table_service: TableService,
        table_repository_mock: AsyncMock,
        schema_manager_mock: MagicMock,
    ):
        table = _tickets()
        table_repository_mock.get_by_datastore_and_name.return_value = table
        schema_manager_mock.change_column_constraints.side_effect = (
            DatastoreColumnChangeRefusedError("a row holds another value")
        )

        with pytest.raises(DatastoreColumnChangeRefusedError):
            await table_service.update_column(
                table.pod_id,
                "tickets",
                "status",
                ["open", "closed", "waiting"],
                ctx=allow_all_context(user_id=uuid4(), pod_id=table.pod_id),
            )

        table_repository_mock.update.assert_not_awaited()
        assert _status(table).options == ["open", "closed"]
        assert table.collect_events() == []


class _ConstraintNames:
    def __init__(self, names: list[str]):
        self._names = names

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: self._names)


def _manager_on(connection: SimpleNamespace) -> SchemaManager:
    @asynccontextmanager
    async def begin():
        yield connection

    return SchemaManager(engine=SimpleNamespace(begin=begin), session_factory=object())


def _widened_status() -> ColumnChange:
    return ColumnChange(
        column=ColumnSchema(
            name="status",
            type=DatastoreDataType.ENUM,
            options=["open", "closed", "it's waiting"],
        ),
        options_widened=True,
        foreign_key_dropped=False,
    )


@pytest.mark.asyncio
class TestReplacingTheCheck:
    async def test_every_check_on_the_column_is_replaced_under_the_table_lock(self):
        """Found by what it constrains, not by a guessed name: PostgreSQL
        truncates a long one and numbers a clash."""
        connection = SimpleNamespace(
            execute=AsyncMock(
                return_value=_ConstraintNames(["tickets_status_check", 'odd"name'])
            )
        )
        manager = _manager_on(connection)
        pod_id = uuid4()
        relation = f'"{manager.get_schema_name(pod_id)}"."tickets"'

        await manager.change_column_constraints(pod_id, "tickets", _widened_status())

        calls = connection.execute.await_args_list[1:]  # after the lock timeout
        statements = [str(c.args[0]) for c in calls]
        assert statements[0] == f"LOCK TABLE {relation} IN ACCESS EXCLUSIVE MODE"
        assert "pg_constraint" in statements[1]
        assert calls[1].args[1] == {
            "column": "status",
            "relation": relation,
            "kind": "c",
        }
        new_check = """CHECK ("status" IN ('open', 'closed', 'it''s waiting'))"""
        assert statements[2:] == [
            f'ALTER TABLE {relation} DROP CONSTRAINT "tickets_status_check"',
            f'ALTER TABLE {relation} DROP CONSTRAINT "odd""name"',
            f"ALTER TABLE {relation} ADD {new_check}",
        ]

    async def test_a_row_outside_the_new_options_refuses_the_change(self):
        violation = DBAPIError(
            "ALTER TABLE", {}, SimpleNamespace(sqlstate="23514"), False
        )

        async def _execute(statement, *args):
            if "ADD CHECK" in str(statement):
                raise violation
            return _ConstraintNames(["tickets_status_check"])

        manager = _manager_on(SimpleNamespace(execute=AsyncMock(side_effect=_execute)))

        with pytest.raises(DatastoreColumnChangeRefusedError) as refused:
            await manager.change_column_constraints(
                uuid4(), "tickets", _widened_status()
            )

        assert refused.value.status_code == 422
        assert refused.value.details["field"] == "status"


def _with_customer_link() -> DatastoreTableEntity:
    table = _tickets()
    table.columns.append(
        ColumnSchema.model_validate(
            {
                "name": "customer_id",
                "type": "UUID",
                "foreign_key": {"references": "customers.id"},
            }
        )
    )
    return table


class TestDroppingAForeignKey:
    """A bundle that no longer declares a column's foreign key: `lemma pods
    import` said the table was updated and kept it."""

    def test_the_foreign_key_goes_and_the_column_stays(self):
        change = column_change(
            _with_customer_link(), "customer_id", None, drop_foreign_key=True
        )

        assert change is not None
        assert change.foreign_key_dropped and not change.options_widened
        assert change.column.name == "customer_id"
        assert change.column.foreign_key is None

    def test_a_column_with_none_is_left_as_it_is(self):
        assert column_change(_tickets(), "title", None, drop_foreign_key=True) is None

    def test_options_and_the_foreign_key_change_together(self):
        table = _with_customer_link()
        assert column_change(table, "status", ["open", "closed", "x"], True) == (
            ColumnChange(
                column=widened_enum_column(table, "status", ["open", "closed", "x"]),
                options_widened=True,
                foreign_key_dropped=False,
            )
        )


@pytest.mark.asyncio
async def test_the_foreign_key_is_dropped_by_what_it_constrains_with_no_check():
    connection = SimpleNamespace(
        execute=AsyncMock(return_value=_ConstraintNames(["tickets_customer_id_fkey"]))
    )
    manager = _manager_on(connection)
    pod_id = uuid4()
    relation = f'"{manager.get_schema_name(pod_id)}"."tickets"'
    column = _with_customer_link().columns[-1]

    await manager.change_column_constraints(
        pod_id,
        "tickets",
        ColumnChange(
            column=column.model_copy(update={"foreign_key": None}),
            options_widened=False,
            foreign_key_dropped=True,
        ),
    )

    calls = connection.execute.await_args_list[1:]  # after the lock timeout
    statements = [str(c.args[0]) for c in calls]
    assert calls[1].args[1]["kind"] == "f"
    assert statements[2:] == [
        f'ALTER TABLE {relation} DROP CONSTRAINT "tickets_customer_id_fkey"'
    ]


@pytest.mark.asyncio
async def test_the_change_waits_a_bounded_time_for_the_table():
    """A waiting ACCESS EXCLUSIVE request queues every later query on the
    table; one that cannot have the table soon gives up, as a 409."""
    from app.modules.datastore.domain.errors import DatastoreConflictError

    timed_out = DBAPIError("LOCK TABLE", {}, SimpleNamespace(sqlstate="55P03"), False)

    async def _execute(statement, *args):
        if str(statement).startswith("LOCK TABLE"):
            raise timed_out
        return _ConstraintNames([])

    connection = SimpleNamespace(execute=AsyncMock(side_effect=_execute))
    manager = _manager_on(connection)

    with pytest.raises(DatastoreConflictError, match="in use"):
        await manager.change_column_constraints(uuid4(), "tickets", _widened_status())
    first = str(connection.execute.await_args_list[0].args[0])
    assert first.startswith("SET LOCAL lock_timeout = ")


@pytest.mark.asyncio
async def test_an_option_with_a_colon_is_not_read_as_a_bind_parameter():
    connection = SimpleNamespace(
        execute=AsyncMock(return_value=_ConstraintNames(["tickets_status_check"]))
    )
    manager = _manager_on(connection)
    status_change = _widened_status()
    # `:triage` is what text() would take for a bind parameter: a colon after a
    # word character (`ref:42`) it leaves alone either way.
    column = status_change.column.model_copy(
        update={"options": [*status_change.column.options, ":triage"]}
    )

    await manager.change_column_constraints(
        uuid4(),
        "tickets",
        ColumnChange(column=column, options_widened=True, foreign_key_dropped=False),
    )

    added = connection.execute.await_args_list[-1].args[0]
    assert not added._bindparams
    assert "':triage'" in str(added.compile())
