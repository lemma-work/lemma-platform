"""Changing an existing column's constraints: the CHECK that holds an ENUM
column to its options, and its foreign key."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.datastore.domain.datastore_entities import ColumnSchema
from app.modules.datastore.domain.errors import (
    DatastoreColumnChangeRefusedError,
    DatastoreConflictError,
    DatastoreValidationError,
)

#: SQLSTATE ``check_violation``: ``ADD CHECK`` found an existing row it rejects.
_CHECK_VIOLATION = "23514"
_LOCK_NOT_AVAILABLE = "55P03"
#: How long the change waits for the table. Every later query on the table
#: queues behind a waiting ACCESS EXCLUSIVE request, so a change that cannot
#: have the table soon gives up rather than stall its reads and writes.
_LOCK_TIMEOUT_MS = 5_000

# A constraint of one kind (`c` check, `f` foreign key) on this one column and
# nothing else.
_COLUMN_CONSTRAINTS_SQL = text(
    "SELECT con.conname FROM pg_constraint con "
    "JOIN pg_attribute att "
    "ON att.attrelid = con.conrelid AND att.attname = :column "
    "WHERE con.contype::text = :kind "
    "AND con.conrelid = to_regclass(:relation) "
    "AND con.conkey = ARRAY[att.attnum]"
)


async def replace_column_check(
    engine: AsyncEngine,
    relation: str,
    column: ColumnSchema,
    check_clause: str | None,
    *,
    drop_foreign_key: bool = False,
) -> None:
    """Swap ``column``'s CHECK on ``relation`` (quoted, schema-qualified) for
    ``check_clause`` (``None`` leaves it), and drop its foreign key when
    ``drop_foreign_key``.

    The CHECK was declared inline with the column, so PostgreSQL named it, and
    the name is not reliably ``<table>_<column>_check``: a long one is
    truncated and a clash gets a number. It is found instead by what it
    constrains. Drop and add share one transaction under the table's lock, so
    no write lands between them, and adding the CHECK validates every existing
    row: a row outside the new options fails the change rather than surviving
    it. Any other database error is the caller's to map.
    """
    if check_clause is not None and not check_clause:
        raise DatastoreValidationError(
            f"Column '{column.name}' has no ENUM options to enforce"
        )
    try:
        async with engine.begin() as conn:
            # Bounds waits for locks (this table's, and a dropped foreign
            # key's other table), never the scan the new CHECK makes.
            await conn.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT_MS}ms'"))
            # Before reading the catalog, so a concurrent change to the same
            # column cannot add a check this transaction never sees.
            await conn.execute(text(f"LOCK TABLE {relation} IN ACCESS EXCLUSIVE MODE"))
            kinds = ["f"] if drop_foreign_key else []
            if check_clause is not None:
                kinds.append("c")
            for kind in kinds:
                existing = await conn.execute(
                    _COLUMN_CONSTRAINTS_SQL,
                    {"column": column.name, "relation": relation, "kind": kind},
                )
                for constraint_name in existing.scalars().all():
                    quoted = constraint_name.replace('"', '""')
                    await conn.execute(
                        text(f'ALTER TABLE {relation} DROP CONSTRAINT "{quoted}"')
                    )
            if check_clause is not None:
                # `text()` reads ":word" as a bind parameter, and an option
                # may well hold a colon ("ref:42"): escaped, it stays literal.
                literal = check_clause.replace(":", "\\:")
                await conn.execute(text(f"ALTER TABLE {relation} ADD{literal}"))
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate == _LOCK_NOT_AVAILABLE:
            raise DatastoreConflictError(
                f"The table is in use, so column '{column.name}' was not "
                "changed; try again shortly."
            ) from exc
        if sqlstate != _CHECK_VIOLATION:
            raise
        raise DatastoreColumnChangeRefusedError(
            f"Some rows hold a value of column '{column.name}' that is not one "
            "of the new options.",
            details={"field": column.name, "allowed_values": column.options},
        ) from exc
