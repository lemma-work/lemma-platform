"""Record-event construction and durable outbox dispatch policy.

Events carry the row so a subscriber can read a column the writer never
mentioned. That convenience is unbounded, and unbounded is what broke: a table
column holding a document produced 3.4MB stream entries -- the new row plus the
prior value of every changed column -- in a stream capped by entry count rather
than bytes. Above a budget the body is dropped and flagged, and the consumer
that actually needs it reads the row it was just told about.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from app.modules.datastore.domain.events import (
    DatastoreRecordEvent,
    DatastoreRecordOperation,
)
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.services.record_size_policy import exceeds
from app.modules.datastore.services.table_context import TableContext


class RecordEventCoordinator:
    def __init__(
        self,
        *,
        dispatcher: Callable[[], Awaitable[int]] | None,
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
        changed: list[str] | None = None,
        previous: dict[str, Any] | None = None,
    ) -> DatastoreRecordEvent | None:
        if not ctx.events_enabled:
            return None
        event_owner = (owner_user_id or user_id) if ctx.enable_rls else None
        budget = datastore_settings.datastore_event_payload_max_bytes
        payload_truncated = exceeds(payload, budget)
        # `previous` is measured on its own rather than against what is left of
        # the payload's budget: the two answer different questions, and one
        # large column should not silently strip the other's prior image.
        previous_truncated = previous is not None and exceeds(previous, budget)
        return DatastoreRecordEvent.create(
            pod_id=ctx.pod_id,
            table_name=ctx.table_name,
            record_id=str(record_id),
            operation=operation,
            payload={} if payload_truncated else payload,
            changed=changed,
            previous=None if previous_truncated else previous,
            actor_id=user_id,
            owner_user_id=event_owner,
            payload_truncated=payload_truncated,
            previous_truncated=previous_truncated,
        )

    def required_for_record(
        self,
        record,
        changed: list[str] | None = None,
        previous: dict[str, Any] | None = None,
        *,
        ctx: TableContext,
        operation: DatastoreRecordOperation,
        user_id: UUID,
    ) -> DatastoreRecordEvent:
        """Build the event for a row the repository has just written.

        The payload is the row itself rather than what the caller submitted, so
        a subscriber can read a column the writer never mentioned — a default
        that the database filled in, or a field left alone by an update. The
        repository supplies ``changed`` and ``previous`` because only it knows
        which columns the statement actually wrote.
        """
        event = self.build(
            ctx,
            str(record.id),
            operation,
            record.data,
            user_id,
            owner_user_id=record.user_id,
            changed=changed,
            previous=previous,
        )
        assert event is not None
        return event

    async def dispatch(self) -> None:
        """Optionally notify an external dispatcher after the durable commit.

        Production API requests deliberately have no dispatcher callback. The
        worker-owned transactional outbox loop publishes staged events without
        making record-write latency depend on Redis or per-event acknowledgement
        writes.
        """
        if self.dispatcher is not None:
            await self.dispatcher()
