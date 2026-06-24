"""Unit tests for the shared DB error parser and ENUM pre-validation."""

from __future__ import annotations

import pytest

from app.modules.datastore.domain.datastore_entities import ColumnSchema, DatastoreDataType, ForeignKeySpec
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreInfrastructureError,
    DatastoreValidationError,
)
from app.modules.datastore.infrastructure.db_error_parser import parse_db_error
from app.modules.datastore.services.record_validator import RecordValidator
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.domain.datastore_entities import DatastoreTableEntity


def _make_ctx(
    table_name: str = "app_specs",
    columns: list[ColumnSchema] | None = None,
) -> TableContext:
    if columns is None:
        columns = [
            ColumnSchema(name="status", type=DatastoreDataType.ENUM, options=["planned", "active", "done"]),
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
        msg, details, cls = parse_db_error(exc, table_name="app_specs", columns=ctx.columns)

        assert cls is DatastoreValidationError
        assert "draft" in msg.lower() or "value" in msg.lower()
        assert details is not None
        assert details["field"] == "status"
        assert details["allowed_values"] == ["planned", "active", "done"]

    def test_check_violation_non_enum_gives_clean_message(self):
        raw = (
            'new row for relation "items" violates check constraint "items_qty_check"'
        )
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
        msg, details, cls = parse_db_error(exc, table_name="milestones", columns=columns)

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
        msg, details, cls = parse_db_error(exc, table_name="app_specs", operation="create record")

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
        msg, details, cls = parse_db_error(exc, table_name="app_specs", operation="create record")

        assert cls is DatastoreValidationError
        assert "INSERT" not in msg
        assert "secret_value" not in msg
        assert "some weird error" in msg


class TestRecordValidatorEnum:
    def _make_validator(self, columns: list[ColumnSchema] | None = None) -> RecordValidator:
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
        assert any(d.get("field") == "status" and "allowed_values" in d for d in details)

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
