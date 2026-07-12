"""Record-event construction and durable outbox dispatch policy."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from app.modules.datastore.domain.events import (
    DatastoreRecordEvent,
    DatastoreRecordOperation,
)
from app.modules.datastore.services.table_context import TableContext


class RecordEventCoordinator:
    def __init__(
        self,
        *,
        dispatcher: Callable[[], Awaitable[int]],
    ) -> None:
        self.dispatcher = dispatcher

    def build(
        self,
        ctx: TableContext,
        record_id: str,
        operation: DatastoreRecordOperation,
        payload: dict[str, Any],
        user_id: UUID,
        owner_user_id: UUID | None = None,
    ) -> DatastoreRecordEvent | None:
        if not ctx.events_enabled:
            return None
        event_owner = (owner_user_id or user_id) if ctx.enable_rls else None
        return DatastoreRecordEvent.create(
            pod_id=ctx.pod_id,
            table_name=ctx.table_name,
            record_id=str(record_id),
            operation=operation,
            payload=payload,
            actor_id=user_id,
            owner_user_id=event_owner,
        )

    def required_for_record(
        self,
        record,
        *,
        ctx: TableContext,
        operation: DatastoreRecordOperation,
        payload: dict[str, Any],
        user_id: UUID,
    ) -> DatastoreRecordEvent:
        event = self.build(
            ctx,
            str(record.id),
            operation,
            payload,
            user_id,
            owner_user_id=record.user_id,
        )
        assert event is not None
        return event

    async def dispatch(self) -> None:
        await self.dispatcher()
