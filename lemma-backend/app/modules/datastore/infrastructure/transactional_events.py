"""Datastore-local transactional event staging."""

import asyncio
from typing import cast

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert

from app.core.domain.events import DomainEvent
from app.core.infrastructure.events.models import DomainEventOutbox

_outbox_ready = False
_outbox_lock = asyncio.Lock()
_dispatcher = None


async def ensure_datastore_event_outbox() -> None:
    """Create the outbox in an optional separately configured datastore DB.

    The consolidated Alembic revision owns the canonical/main-database table.
    A separate datastore database is provisioned dynamically like its pod
    schemas, so it receives the identical SQLAlchemy table definition here.
    """
    global _outbox_ready
    if _outbox_ready:
        return
    from app.modules.datastore.infrastructure.session import get_datastore_engine

    async with _outbox_lock:
        if _outbox_ready:
            return
        async with get_datastore_engine().begin() as connection:
            outbox_table = cast(Table, DomainEventOutbox.__table__)
            await connection.run_sync(outbox_table.create, checkfirst=True)
        _outbox_ready = True


def reset_datastore_event_outbox_state() -> None:
    global _dispatcher, _outbox_ready
    _outbox_ready = False
    _dispatcher = None


async def dispatch_datastore_outbox_once() -> int:
    """Best-effort latency nudge; durable retry remains worker-owned."""
    global _dispatcher
    from app.core.infrastructure.events.message_bus import get_message_bus
    from app.core.infrastructure.events.outbox import OutboxDispatcher
    from app.modules.datastore.infrastructure.session import (
        get_datastore_session_maker,
    )

    try:
        if _dispatcher is None:
            _dispatcher = OutboxDispatcher(
                get_datastore_session_maker(), get_message_bus()
            )
        return await _dispatcher.dispatch_once()
    except Exception:  # noqa: BLE001 -- the committed outbox is the retry source
        return 0


async def stage_domain_events(session, events: list[DomainEvent]) -> None:
    """Write record events in the same datastore transaction as row changes."""
    if not events:
        return
    rows = [
        {
            "id": event.event_id,
            "stream": event.stream_name(),
            "event_type": event.event_type,
            "schema_version": event.schema_version,
            "producer": event.producer,
            "payload": event.model_dump(mode="json"),
            "occurred_at": event.occurred_at,
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "request_id": event.request_id,
        }
        for event in events
    ]
    await session.execute(
        insert(DomainEventOutbox)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["id"])
    )
