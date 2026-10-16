"""Reading a pod's change log: the one interface every subscriber reads through.

Durable delivery (``changes.deliver``) and the live socket's pod hub both call
:func:`read_after` with a cursor they keep, and nothing else in the platform
reads ``reserved_changes``. The signatures here are a contract between those
two readers; see ``out/work-runtime/CHANGES_API.md``.

The read rule. ``seq`` is assigned when a row is inserted, not when its
transaction commits, so a reader that takes the newest committed rows and moves
its cursor past them can skip a slower writer's lower ``seq`` that commits a
moment later. Every row therefore records its writing transaction, and a read
returns only rows whose transaction is older than every transaction still
running (``xid < pg_snapshot_xmin``). Below that horizon every transaction has
committed or aborted and no new one can get a smaller id, so ``(xid, seq)``
order is final and gap-free there. The cost is that delivery waits for the
oldest open transaction on the database, which the statement and
idle-in-transaction timeouts bound.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import JsonValue
from pydantic_core import to_jsonable_python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.modules.datastore.changes.cursor import (
    ChangePosition,
    decode_cursor,
    encode_cursor,
)
from app.modules.datastore.changes.schema import FLOORS_TABLE, changes_table
from app.modules.datastore.infrastructure import pod_schema_memo
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    CHANGE_LOG_VERSION,
    schema_name_for_pod,
)
from app.modules.datastore.infrastructure.record_bulk_sql import array_element_type
from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.value_converter import ValueConverter

#: The most changes one read returns.
MAX_PAGE = 500

type JsonRow = dict[str, JsonValue]


class ChangeOp(StrEnum):
    INSERT = "I"
    UPDATE = "U"
    DELETE = "D"

    @property
    def verb(self) -> str:
        """``insert`` / ``update`` / ``delete``, as frames and filters spell it."""
        return _VERBS[self]


_VERBS = {
    ChangeOp.INSERT: "insert",
    ChangeOp.UPDATE: "update",
    ChangeOp.DELETE: "delete",
}


@dataclass(frozen=True, slots=True)
class Change:
    """One record change, as the log holds it.

    ``new_row`` and ``old_values`` are present only when a durable subscriber
    watched the table and operation at write time (or the change was converted
    from the old event path, which always carried them). A live reader never
    relies on them: it reads the current row with :func:`fetch_rows`.
    """

    position: ChangePosition
    table_id: UUID
    op: ChangeOp
    record_id: str
    #: ``normal`` | ``bulk`` | ``import``.
    intent: str
    #: The row's owner on an RLS table, else ``None``: what lets a live reader
    #: apply row visibility without a query.
    owner_user_id: UUID | None
    #: ``visitor:<session>`` / ``contact:<id>`` when somebody outside the pod
    #: wrote the content; ``None`` for a member's write.
    outside_actor: str | None
    #: The row after the write (I, U).
    new_row: JsonRow | None
    #: U: prior values of the written columns; D: the whole deleted row.
    old_values: JsonRow | None
    at: datetime

    @property
    def cursor(self) -> str:
        """A cursor positioned just after this change."""
        return encode_cursor(self.position)

    @property
    def changed(self) -> list[str] | None:
        """The columns an update wrote, when its snapshot was kept."""
        if self.op is not ChangeOp.UPDATE or self.old_values is None:
            return None
        return sorted(self.old_values)


@dataclass(frozen=True, slots=True)
class Page:
    changes: list[Change]
    #: Where the next read starts. After a short page this is the horizon,
    #: not the last change: everything below the horizon has been considered,
    #: so a filtered reader does not rescan rows it filtered out.
    cursor: str
    #: The page hit its limit: more changes may already be readable.
    full: bool
    #: The read's horizon: no change below it can still appear.
    horizon: str
    #: The cursor asked for was below what retention has deleted, so changes
    #: in between may be gone; the reader should resync from ``horizon``.
    expired: bool = False


async def current_horizon(pod_id: UUID, *, engine: AsyncEngine | None = None) -> str:
    """A cursor for "from now on": every change not yet final is after it.

    ``(xmin, 0)``: transactions still running when this is read all have
    ``xid >= xmin``, so a subscriber starting here misses nothing that commits
    after it was created.
    """
    del pod_id  # one horizon per database; the argument keeps call sites honest
    async with (engine or get_datastore_engine()).connect() as conn:
        return encode_cursor(await _horizon(conn))


async def read_after(
    pod_id: UUID,
    cursor: str | None,
    *,
    table_ids: Sequence[UUID] | None = None,
    ops: Sequence[ChangeOp] | None = None,
    limit: int = MAX_PAGE,
    engine: AsyncEngine | None = None,
) -> Page:
    """Up to ``limit`` final changes after ``cursor``, in ``(xid, seq)`` order.

    ``cursor`` ``None`` reads from the oldest retained change. ``table_ids`` /
    ``ops`` narrow the page in SQL. Raises ``InvalidCursor`` for a string that
    is not a cursor. A pod whose schema has no change log yet reads as empty.
    """
    limit = max(1, min(limit, MAX_PAGE))
    after = decode_cursor(cursor) if cursor is not None else ChangePosition.start()
    schema = schema_name_for_pod(pod_id)
    where = [
        "(xid, seq) > (CAST(CAST(:xid AS text) AS xid8), :seq)",
        "xid < pg_snapshot_xmin(pg_current_snapshot())",
    ]
    params: dict[str, object] = {
        "xid": str(after.xid),
        "seq": after.seq,
        "limit": limit,
    }
    if table_ids is not None:
        where.append("table_id = ANY(CAST(:table_ids AS uuid[]))")
        params["table_ids"] = [str(table_id) for table_id in table_ids]
    if ops is not None:
        where.append("op = ANY(CAST(:ops AS text[]))")
        params["ops"] = [op.value for op in ops]
    async with (engine or get_datastore_engine()).connect() as conn:
        if not await _has_log(conn, schema):
            horizon = await _horizon(conn)
            return Page(
                [], encode_cursor(max(after, horizon)), False, encode_cursor(horizon)
            )
        horizon, floor = await _horizon_and_floor(conn, pod_id)
        expired = cursor is not None and after < floor
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT xid::text AS xid_text, seq, table_id, op, record_id, intent, "
                        "owner_user_id, outside_actor, new_row, old_values, at "
                        f"FROM {changes_table(schema)} WHERE {' AND '.join(where)} "
                        "ORDER BY xid, seq LIMIT :limit"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
    changes = [_change(row) for row in rows]
    full = len(changes) == limit
    if full:
        next_position = changes[-1].position
    else:
        # The horizon the page was read under may be older than this read's
        # snapshot, never newer, so ``max`` keeps the cursor monotonic.
        last = changes[-1].position if changes else after
        next_position = max(last, horizon)
    return Page(
        changes=changes,
        cursor=encode_cursor(next_position),
        full=full,
        horizon=encode_cursor(horizon),
        expired=expired,
    )


async def oldest_unfinal_after(
    pod_id: UUID,
    cursor: str,
    *,
    table_ids: Sequence[UUID] | None = None,
    ops: Sequence[ChangeOp] | None = None,
    engine: AsyncEngine | None = None,
) -> datetime | None:
    """When the first committed change after ``cursor`` that is not final yet
    was written (its ``at``), or ``None`` when there is none.

    A change becomes readable only once every older transaction on the
    database has ended. One that committed while such a transaction was still
    open is already in the log but above the horizon, and no further write is
    owed to announce it: a reader that came up empty must look again.
    Uncommitted changes are invisible here; their own commit wakes a reader.
    The time says how long such a change has waited.
    """
    after = decode_cursor(cursor)
    schema = schema_name_for_pod(pod_id)
    where = ["(xid, seq) > (CAST(CAST(:xid AS text) AS xid8), :seq)"]
    params: dict[str, object] = {"xid": str(after.xid), "seq": after.seq}
    if table_ids is not None:
        where.append("table_id = ANY(CAST(:table_ids AS uuid[]))")
        params["table_ids"] = [str(table_id) for table_id in table_ids]
    if ops is not None:
        where.append("op = ANY(CAST(:ops AS text[]))")
        params["ops"] = [op.value for op in ops]
    async with (engine or get_datastore_engine()).connect() as conn:
        if not await _has_log(conn, schema):
            return None
        return await conn.scalar(
            text(
                f"SELECT at FROM {changes_table(schema)} "
                f"WHERE {' AND '.join(where)} ORDER BY xid, seq LIMIT 1"
            ),
            params,
        )


async def count_between(
    pod_id: UUID,
    after: ChangePosition,
    until: ChangePosition,
    *,
    table_ids: Sequence[UUID],
    ops: Sequence[ChangeOp],
    engine: AsyncEngine | None = None,
) -> tuple[int, ChangePosition | None]:
    """How many changes lie in ``(after, until)``, and the last of them.

    For a subscriber being moved past what it never read, so what it skipped
    can be said: run once per lapse, never on a delivery path.
    """
    schema = schema_name_for_pod(pod_id)
    async with (engine or get_datastore_engine()).connect() as conn:
        if not await _has_log(conn, schema):
            return 0, None
        row = (
            await conn.execute(
                text(
                    "WITH span AS (SELECT xid, seq FROM "
                    f"{changes_table(schema)} WHERE "
                    "(xid, seq) > (CAST(CAST(:after_xid AS text) AS xid8), :after_seq) "
                    "AND (xid, seq) < (CAST(CAST(:until_xid AS text) AS xid8), :until_seq) "
                    "AND table_id = ANY(CAST(:table_ids AS uuid[])) "
                    "AND op = ANY(CAST(:ops AS text[]))) "
                    "SELECT (SELECT count(*) FROM span), "
                    "(SELECT xid::text FROM span ORDER BY xid DESC, seq DESC LIMIT 1), "
                    "(SELECT seq FROM span ORDER BY xid DESC, seq DESC LIMIT 1)"
                ),
                {
                    "after_xid": str(after.xid),
                    "after_seq": after.seq,
                    "until_xid": str(until.xid),
                    "until_seq": until.seq,
                    "table_ids": [str(table_id) for table_id in table_ids],
                    "ops": [op.value for op in ops],
                },
            )
        ).one()
    if row[1] is None:
        return int(row[0]), None
    return int(row[0]), ChangePosition(int(row[1]), int(row[2]))


async def fetch_rows(
    pod_id: UUID,
    table: TableContext,
    record_ids: Sequence[str],
    *,
    engine: AsyncEngine | None = None,
) -> dict[str, JsonRow]:
    """The current rows for ``record_ids``, keyed by record id; gone ones absent.

    Read as the database owner, without row-level security: the caller decides
    visibility (a live reader by each change's ``owner_user_id``). Values are
    in the record API's JSON form.
    """
    if not record_ids:
        return {}
    if table.pod_id != pod_id:
        raise ValueError("table belongs to another pod")
    sql, params = fetch_rows_statement(table, record_ids)
    async with (engine or get_datastore_engine()).connect() as conn:
        rows = (await conn.execute(text(sql), params)).mappings().all()
    pk = table.primary_key_column
    found: dict[str, JsonRow] = {}
    for row in rows:
        data = ValueConverter.deserialize_record(dict(row), table.columns)
        found[str(row[pk])] = to_jsonable_python(data)
    return found


def fetch_rows_statement(
    table: TableContext, record_ids: Sequence[str]
) -> tuple[str, dict[str, object]]:
    """The read :func:`fetch_rows` runs: by the typed primary key, so it is an
    index lookup however large the table."""
    pk = table.primary_key_column
    source = f'SELECT * FROM "{table.schema_name}"."{table.table_name}"'
    element = array_element_type(table, pk)
    if element is None:
        # A key type that cannot be named: compare as text, without the index.
        return (
            f'{source} WHERE "{pk}"::text = ANY(CAST(:ids AS text[]))',
            {"ids": list(record_ids)},
        )
    return (
        f'{source} WHERE "{pk}" = ANY(CAST(:ids AS {element}[]))',
        {"ids": [table.parse_primary_key(record_id) for record_id in record_ids]},
    )


async def _horizon(conn: AsyncConnection) -> ChangePosition:
    xmin = await conn.scalar(
        text("SELECT pg_snapshot_xmin(pg_current_snapshot())::text")
    )
    return ChangePosition(int(xmin or 0), 0)


async def _has_log(conn: AsyncConnection, schema: str) -> bool:
    if pod_schema_memo.known_version(schema) >= CHANGE_LOG_VERSION:
        return True
    return bool(
        await conn.scalar(
            text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": changes_table(schema)},
        )
    )


async def _horizon_and_floor(
    conn: AsyncConnection, pod_id: UUID
) -> tuple[ChangePosition, ChangePosition]:
    """One round trip: a pod with a change log has the floors table too."""
    row = (
        await conn.execute(
            text(
                "SELECT pg_snapshot_xmin(pg_current_snapshot())::text, "
                f"(SELECT floor_xid::text FROM {FLOORS_TABLE} WHERE pod_id = :pod_id), "
                f"(SELECT floor_seq FROM {FLOORS_TABLE} WHERE pod_id = :pod_id)"
            ),
            {"pod_id": str(pod_id)},
        )
    ).one()
    horizon = ChangePosition(int(row[0] or 0), 0)
    if row[1] is None:
        return horizon, ChangePosition.start()
    return horizon, ChangePosition(int(row[1]), int(row[2]))


def _change(row: Mapping[str, object]) -> Change:
    owner = row["owner_user_id"]
    at = row["at"]
    assert isinstance(at, datetime)
    return Change(
        position=ChangePosition(int(str(row["xid_text"])), int(str(row["seq"]))),
        table_id=UUID(str(row["table_id"])),
        op=ChangeOp(str(row["op"])),
        record_id=str(row["record_id"]),
        intent=str(row["intent"]),
        owner_user_id=UUID(str(owner)) if owner is not None else None,
        outside_actor=str(row["outside_actor"]) if row["outside_actor"] else None,
        new_row=_json_row(row["new_row"]),
        old_values=_json_row(row["old_values"]),
        at=at,
    )


def _json_row(value: object) -> JsonRow | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"change snapshot is not an object: {type(value).__name__}")
    return {str(key): to_jsonable_python(item) for key, item in value.items()}


__all__ = [
    "MAX_PAGE",
    "Change",
    "ChangeOp",
    "JsonRow",
    "Page",
    "count_between",
    "current_horizon",
    "fetch_rows",
    "fetch_rows_statement",
    "oldest_unfinal_after",
    "read_after",
]
