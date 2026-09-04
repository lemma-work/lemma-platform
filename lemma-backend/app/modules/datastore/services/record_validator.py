"""Validation rules for datastore records against a table schema.

Extracted from ``TableContext`` (which is now a pure schema/operation data
holder) so record validation lives in one focused, unit-testable place.
"""

from __future__ import annotations

from typing import Any

from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
)
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.services.record_size_policy import configured_size_errors
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.value_converter import ValueConverter


def convert_record(
    columns: list[ColumnSchema],
    data: dict[str, Any],
    *,
    skip_computed: bool = True,
    skip_auto: bool = True,
) -> dict[str, Any]:
    """Type-convert a record against its columns, raising a domain error."""
    try:
        return ValueConverter.convert_record(
            data, columns, skip_computed=skip_computed, skip_auto=skip_auto
        )
    except ValueError as exc:
        raise DatastoreValidationError(str(exc)) from exc


_PK_AUTO_TYPES = {
    DatastoreDataType.UUID,
    DatastoreDataType.USER,
    DatastoreDataType.SERIAL,
}
_SYSTEM_OVERRIDABLE_COLUMNS = {"created_at", "updated_at"}


class RecordValidator:
    """Validates record payloads against a table's column schema."""

    def __init__(self, ctx: TableContext) -> None:
        self.ctx = ctx

    @staticmethod
    def allows_creation_override(column: ColumnSchema) -> bool:
        """System timestamp columns whose values a creator may explicitly set."""
        return column.system and column.name in _SYSTEM_OVERRIDABLE_COLUMNS

    def strip_system_write_overrides(self, data: dict[str, Any]) -> dict[str, Any]:
        """Drop creator-supplied values for system-managed timestamp columns."""
        return {
            key: value
            for key, value in data.items()
            if not (
                (column := self.ctx.get_column(key)) is not None
                and self.allows_creation_override(column)
            )
        }

    @staticmethod
    def _can_auto_generate_primary_key(pk_col: ColumnSchema) -> bool:
        return (
            pk_col.auto or pk_col.default is not None or pk_col.type in _PK_AUTO_TYPES
        )

    def validate_update(self, data: dict[str, Any]) -> None:
        """Refuse an update payload, or return.

        The create-side rules live in :meth:`validate`, which reports rather
        than raises because a creator wants every problem at once. An update is
        checked column by column against what was actually submitted, so it
        raises on the first set of findings. Both go through
        :func:`configured_size_errors`, so "too large" cannot mean two
        different things depending on which way a row is written.
        """
        ctx = self.ctx
        errors: list[str] = []
        error_details: list[dict[str, Any]] = []

        for key, value in data.items():
            column = ctx.get_column(key)
            if column is None:
                continue
            if key == ctx.primary_key_column:
                errors.append(f"Cannot modify primary key column '{key}'")
                error_details.append({"field": key, "reason": "primary_key"})
            elif column.computed:
                errors.append(f"Cannot provide value for computed column '{key}'")
                error_details.append({"field": key, "reason": "computed"})
            elif column.system and not self.allows_creation_override(column):
                errors.append(f"Cannot provide value for system-managed column '{key}'")
                error_details.append({"field": key, "reason": "system_managed"})
            elif (
                column.type == DatastoreDataType.ENUM
                and column.options
                and value is not None
                and value not in column.options
            ):
                allowed = ", ".join(column.options)
                errors.append(
                    f"Value '{value}' is not allowed for column '{key}'. "
                    f"Allowed values: {allowed}"
                )
                error_details.append(
                    {
                        "field": key,
                        "reason": "enum",
                        "value": value,
                        "allowed_values": column.options,
                    }
                )

        size_messages, size_details = configured_size_errors(data)
        errors.extend(size_messages)
        error_details.extend(size_details)

        if errors:
            raise DatastoreValidationError(
                f"Invalid record data: {'; '.join(errors)}",
                details={"errors": error_details},
            )

    def validate(
        self,
        data: dict[str, Any],
        *,
        is_creation: bool = False,
    ) -> tuple[bool, list[str], list[dict[str, Any]]]:
        """Validate ``data`` against the table schema.

        Returns ``(is_valid, error_messages, error_details)`` where
        ``error_messages`` are human-readable strings and ``error_details``
        is a list of structured dicts suitable for the ``details`` field of
        :class:`DatastoreValidationError`.
        """
        ctx = self.ctx
        errors: list[str] = []
        details: list[dict[str, Any]] = []
        column_map = {col.name: col for col in ctx.columns}
        pk_col = ctx.get_primary_key_schema() if is_creation else None
        pk_auto_capable = (
            self._can_auto_generate_primary_key(pk_col) if pk_col else False
        )

        for col in ctx.columns:
            if col.computed and col.name in data:
                errors.append(f"Cannot provide value for computed column '{col.name}'")
                details.append({"field": col.name, "reason": "computed"})
            if (
                is_creation
                and col.auto
                and col.name in data
                and col.name != ctx.primary_key_column
            ):
                if self.allows_creation_override(col):
                    continue
                kind = "system-managed" if col.system else "auto-generated"
                errors.append(f"Cannot provide value for {kind} column '{col.name}'")
                details.append({"field": col.name, "reason": kind})
            if (
                col.required
                and col.name not in data
                and col.name != ctx.primary_key_column
                and not col.auto
                and not col.computed
                and col.default is None
            ):
                errors.append(f"Missing required column '{col.name}'")
                details.append({"field": col.name, "reason": "required"})

        if is_creation and ctx.primary_key_column not in data and not pk_auto_capable:
            errors.append(f"Missing primary key column '{ctx.primary_key_column}'")
            details.append({"field": ctx.primary_key_column, "reason": "primary_key"})

        for key, value in data.items():
            col = column_map.get(key)
            if (
                col is not None
                and value is None
                and col.required
                and col.default is None
            ):
                errors.append(f"Column '{key}' cannot be null")
                details.append({"field": key, "reason": "not_null"})

            if (
                col is not None
                and col.type == DatastoreDataType.ENUM
                and col.options
                and value is not None
                and value not in col.options
            ):
                allowed = ", ".join(col.options)
                errors.append(
                    f"Value '{value}' is not allowed for column '{key}'. "
                    f"Allowed values: {allowed}"
                )
                details.append(
                    {
                        "field": key,
                        "reason": "enum",
                        "value": value,
                        "allowed_values": col.options,
                    }
                )

        size_messages, size_details = configured_size_errors(data)
        errors.extend(size_messages)
        details.extend(size_details)

        return (len(errors) == 0, errors, details)
