"""Record-event construction and durable/compatibility dispatch policy."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from app.core.domain.message_bus import MessageBus
from app.modules.datastore.domain.events import (
    DATASTORE_EVENTS_STREAM,
    DatastoreRecordEvent,
    DatastoreRecordOperation,
)
from app.modules.datastore.services.table_context import TableContext


class RecordEventCoordinator:
    def __init__(
        self,
        message_bus: MessageBus,
        *,
        transactional: bool,
        dispatcher: Callable[[], Awaitable[int]] | None,
    ) -> None:
        self.message_bus = message_bus
        self.transactional = transactional
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

    async def publish(self, event: DatastoreRecordEvent) -> None:
        await self.message_bus.publish(DATASTORE_EVENTS_STREAM, event)

    async def emit_compat(
        self,
        ctx: TableContext,
        record_id: str,
        operation: DatastoreRecordOperation,
        payload: dict[str, Any],
        user_id: UUID,
        owner_user_id: UUID | None = None,
    ) -> None:
        event = self.build(
            ctx, record_id, operation, payload, user_id, owner_user_id
        )
        if event is not None:
            await self.publish(event)

    async def dispatch(self) -> None:
        if self.dispatcher is not None:
            await self.dispatcher()
