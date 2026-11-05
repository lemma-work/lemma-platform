"""``LiveChangeFeedPort`` over the pod change log reader."""

from __future__ import annotations

import time
from collections.abc import Sequence
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.datastore.changes import reader
from app.modules.datastore.changes.cursor import InvalidCursor, decode_cursor
from app.modules.datastore.domain.live import (
    ChangePosition,
    LiveChange,
    LiveCursorExpired,
    LiveOp,
    LivePage,
    RowPayload,
)
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    schema_name_for_pod,
)
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.wiring import build_table_service

#: A table's columns are cached this long. A column added in the meantime is
#: missing from rows for at most this long; the record is still delivered.
_TABLE_TTL_SECONDS = 30.0


_OPS: dict[reader.ChangeOp, LiveOp] = {
    reader.ChangeOp.INSERT: "insert",
    reader.ChangeOp.UPDATE: "update",
    reader.ChangeOp.DELETE: "delete",
}


def _live_change(change: reader.Change) -> LiveChange:
    return LiveChange(
        position=(change.position.xid, change.position.seq),
        table_id=change.table_id,
        op=_OPS[change.op],
        record_id=change.record_id,
        owner_user_id=change.owner_user_id,
        at=change.at,
    )


class ChangeLogFeed:
    """One per hub: it caches the hub's tables' shapes for row decoding."""

    def __init__(self) -> None:
        self._uow_factory = SessionUnitOfWorkFactory(async_session_maker)
        self._tables: dict[UUID, tuple[float, TableContext | None]] = {}

    async def horizon(self, pod_id: UUID) -> str:
        return await reader.current_horizon(pod_id)

    async def read(
        self,
        pod_id: UUID,
        cursor: str,
        *,
        table_ids: Sequence[UUID] | None,
        limit: int,
    ) -> LivePage:
        try:
            page = await reader.read_after(
                pod_id, cursor, table_ids=table_ids, limit=limit
            )
        except InvalidCursor as exc:
            raise LiveCursorExpired(cursor) from exc
        if page.expired:
            raise LiveCursorExpired(cursor)
        return LivePage(
            changes=tuple(_live_change(change) for change in page.changes),
            cursor=page.cursor,
            more=page.full,
        )

    async def rows(
        self, pod_id: UUID, table_id: UUID, record_ids: Sequence[str]
    ) -> dict[str, RowPayload]:
        table = await self._table(pod_id, table_id)
        if table is None:
            return {}
        return await reader.fetch_rows(pod_id, table, record_ids)

    def position(self, cursor: str) -> ChangePosition:
        try:
            decoded = decode_cursor(cursor)
        except InvalidCursor as exc:
            raise LiveCursorExpired(cursor) from exc
        return (decoded.xid, decoded.seq)

    async def _table(self, pod_id: UUID, table_id: UUID) -> TableContext | None:
        cached = self._tables.get(table_id)
        now = time.monotonic()
        if cached is not None and now - cached[0] < _TABLE_TTL_SECONDS:
            return cached[1]
        async with self._uow_factory() as uow:
            entity = await build_table_service(uow).table_repository.get(table_id)
        context = (
            TableContext.from_table_entity(entity, schema_name_for_pod(pod_id))
            if entity is not None and entity.pod_id == pod_id
            else None
        )
        self._tables[table_id] = (now, context)
        return context
