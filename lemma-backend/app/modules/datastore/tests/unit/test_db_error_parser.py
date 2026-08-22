"""Unit tests for the shared DB error parser and ENUM pre-validation."""

from __future__ import annotations

from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    ForeignKeySpec,
)
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreInfrastructureError,
    DatastoreValidationError,
)
from app.modules.datastore.infrastructure.db_error_parser import parse_db_error
from app.modules.datastore.services.record_validator import RecordValidator
from app.modules.datastore.services.table_context import TableContext


def _make_ctx(
    table_name: str = "app_specs",
    columns: list[ColumnSchema] | None = None,
) -> TableContext:
    if columns is None:
        columns = [
            ColumnSchema(
                name="status",
                type=DatastoreDataType.ENUM,
                options=["planned", "active", "done"],
            ),
            ColumnSchema(name="title", type=DatastoreDataType.TEXT, required=True),
            ColumnSchema(name="priority", type=DatastoreDataType.INTEGER),
        ]
    return TableContext(
        pod_id=__import__("uuid").uuid4(),
        table_id=__import__("uuid").uuid4(),
        table_name=table_name,
        schema_name="pod_test",
        columns=columns,
        primary_key_column="id",
        enable_rls=False,
    )


class TestParseDbError:
    def test_check_violation_enum_extracts_allowed_values(self):
        raw = (
            "<class 'asyncpg.exceptions.CheckViolationError'>: new row for relation "
            '"app_specs" violates check constraint "app_specs_status_check"\n'
            "DETAIL:  Failing row contains (fbc611d3, 2026-06-24, draft).\n"
            "[SQL: INSERT INTO ...]\n"
            "[parameters: ('draft')]"
        )
        exc = Exception(raw)
        ctx = _make_ctx()
        msg, details, cls = parse_db_error(
            exc, table_name="app_specs", columns=ctx.columns
        )

        assert cls is DatastoreValidationError
        assert "draft" in msg.lower() or "value" in msg.lower()
        assert details is not None
        assert details["field"] == "status"
        assert details["allowed_values"] == ["planned", "active", "done"]

    def test_check_violation_non_enum_gives_clean_message(self):
        raw = 'new row for relation "items" violates check constraint "items_qty_check"'
        exc = Exception(raw)
        msg, details, cls = parse_db_error(exc, table_name="items")

        assert cls is DatastoreValidationError
        assert "qty" in msg.lower()
        assert details is not None
        assert details["field"] == "qty"

    def test_not_null_violation(self):
        raw = 'null value in column "title" of relation "app_specs" violates not-null constraint'
        exc = Exception(raw)
        msg, details, cls = parse_db_error(exc, table_name="app_specs")

        assert cls is DatastoreValidationError
        assert "title" in msg
        assert "required" in msg.lower()
        assert details == {"field": "title"}

    def test_foreign_key_violation(self):
        raw = (
            'insert or update on table "milestones" violates foreign key constraint '
            '"milestones_project_id_fkey"'
        )
        exc = Exception(raw)
        columns = [
            ColumnSchema(
                name="project_id",
                type=DatastoreDataType.UUID,
                required=True,
                foreign_key=ForeignKeySpec(references="projects.id"),
            ),
        ]
        msg, details, cls = parse_db_error(
            exc, table_name="milestones", columns=columns
        )

        assert cls is DatastoreValidationError
        assert "project_id" in msg
        assert "non-existent" in msg.lower()
        assert details == {"field": "project_id", "references": "projects.id"}

    def test_unique_violation(self):
        raw = (
            'duplicate key value violates unique constraint "app_specs_title_key"\n'
            "DETAIL:  Key (title)=(My App) already exists."
        )
        exc = Exception(raw)
        msg, details, cls = parse_db_error(exc, table_name="app_specs")

        assert cls is DatastoreConflictError
        assert "title" in msg
        assert "already exists" in msg.lower()
        assert details == {"field": "title"}

    def test_invalid_input_syntax(self):
        raw = 'invalid input syntax for type uuid: "not-a-uuid"'
        exc = Exception(raw)
        msg, details, cls = parse_db_error(exc, table_name="app_specs")

        assert cls is DatastoreValidationError
        assert "uuid" in msg.lower() or "expected" in msg.lower()

    def test_connection_error_is_infrastructure(self):
        raw = "connection refused\nserver closed the connection unexpectedly"
        exc = Exception(raw)
        msg, details, cls = parse_db_error(
            exc, table_name="app_specs", operation="create record"
        )

        assert cls is DatastoreInfrastructureError
        assert "connectivity" in msg.lower()

    def test_fallback_strips_sql_and_params(self):
        raw = (
            "some weird error\n"
            "DETAIL:  something.\n"
            "[SQL: INSERT INTO foo VALUES ($1)]\n"
            "[parameters: ('secret_value')]\n"
            "(Background on this error at: https://sqlalche.me/e/20/gkpj)"
        )
        exc = Exception(raw)
        msg, details, cls = parse_db_error(
            exc, table_name="app_specs", operation="create record"
        )

        assert cls is DatastoreValidationError
        assert "INSERT" not in msg
        assert "secret_value" not in msg
        assert "some weird error" in msg

    def test_undefined_column_is_a_clean_message_not_a_driver_class(self):
        raw = (
            "<class 'asyncpg.exceptions.UndefinedColumnError'>: column "
            '"no_such_column" does not exist\n'
            "[SQL: SELECT no_such_column FROM ...]"
        )
        exc = Exception(raw)
        msg, details, cls = parse_db_error(
            exc, table_name="app_specs", operation="query execution"
        )

        assert cls is DatastoreValidationError
        assert "asyncpg" not in msg
        assert "<class" not in msg
        assert "no_such_column" in msg
        assert details == {"field": "no_such_column"}

    def test_undefined_table_is_a_clean_message(self):
        raw = (
            "<class 'asyncpg.exceptions.UndefinedTableError'>: relation "
            '"ghost" does not exist'
        )
        exc = Exception(raw)
        msg, details, cls = parse_db_error(exc, operation="query execution")

        assert cls is DatastoreValidationError
        assert "asyncpg" not in msg
        assert "ghost" in msg

    def test_unmatched_asyncpg_error_never_leaks_the_class_name(self):
        # An error with no dedicated branch still must not carry the driver's
        # internal class name through the fallback path.
        raw = "<class 'asyncpg.exceptions.SomeNovelError'>: something odd happened"
        exc = Exception(raw)
        msg, _details, cls = parse_db_error(exc, operation="query execution")

        assert cls is DatastoreValidationError
        assert "<class" not in msg
        assert "asyncpg" not in msg
        assert "something odd happened" in msg


class TestRecordValidatorEnum:
    def _make_validator(
        self, columns: list[ColumnSchema] | None = None
    ) -> RecordValidator:
        ctx = _make_ctx(columns=columns)
        return RecordValidator(ctx)

    def test_enum_invalid_value_rejected_at_creation(self):
        validator = self._make_validator()
        is_valid, errors, details = validator.validate(
            {"title": "My App", "status": "draft"},
            is_creation=True,
        )
        assert not is_valid
        assert any("draft" in e for e in errors)
        assert any("planned" in e and "active" in e and "done" in e for e in errors)
        assert any(
            d.get("field") == "status" and "allowed_values" in d for d in details
        )

    def test_enum_valid_value_accepted(self):
        validator = self._make_validator()
        is_valid, errors, details = validator.validate(
            {"title": "My App", "status": "active"},
            is_creation=True,
        )
        assert is_valid
        assert errors == []

    def test_enum_none_value_skipped(self):
        validator = self._make_validator()
        is_valid, errors, details = validator.validate(
            {"title": "My App", "status": None},
            is_creation=True,
        )
        assert is_valid

    def test_enum_update_rejects_invalid_value(self):
        validator = self._make_validator()
        is_valid, errors, details = validator.validate(
            {"status": "archived"},
            is_creation=False,
        )
        assert not is_valid
        assert any("archived" in e for e in errors)


class TestEveryParsedErrorAcceptsDetails:
    """`parse_db_error` hands back a class its callers construct uniformly.

    All three call sites -- `db_error_parser.raise_from_db_error`,
    `record_errors`, and `sql_identifiers` -- do the same thing with the
    result:

        message, details, error_cls = parse_db_error(...)
        if details is not None:
            raise error_cls(message, details) from exc
        raise error_cls(message) from exc

    So every class the parser can return has to take `(message, details)`.
    `DatastoreInfrastructureError` did not: it accepted `message` alone, and
    the two-argument call raised `TypeError: __init__() takes 2 positional
    arguments but 3 were given` -- replacing the real error and discarding the
    `from exc` chain on the database-failure path, where the cause matters most.

    That branch is not reachable today, because the parser only ever returns
    the infrastructure class with `details=None`. It is one new parser branch
    away from being reachable, and the failure would surface only during a
    database outage. This pins the contract rather than that one class.
    """

    def _parsed_error_classes(self) -> set[type]:
        return {
            DatastoreValidationError,
            DatastoreInfrastructureError,
            DatastoreConflictError,
        }

    def test_each_class_accepts_a_message_and_details(self):
        details = {"column": "title", "reason": "example"}
        for error_cls in self._parsed_error_classes():
            error = error_cls("something failed", details)
            assert error.details == details, error_cls.__name__

    def test_each_class_still_accepts_a_message_alone(self):
        for error_cls in self._parsed_error_classes():
            assert error_cls("something failed").details is None, error_cls.__name__
