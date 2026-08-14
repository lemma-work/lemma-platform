"""Datastore-local transactional event staging."""

import asyncio
from typing import cast

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert

from app.core.domain.events import DomainEvent
from app.core.infrastructure.events.models import DomainEventOutbox

_outbox_ready = False
_outbox_lock = asyncio.Lock()


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
    global _outbox_ready
    _outbox_ready = False


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
    # executemany, not `.values(rows)`.
    #
    # `.values()` with a list renders one INSERT carrying every row inline, so
    # the compiled SQL depends on how many rows there are -- which means the
    # statement cache misses on every batch size it has not seen, and each miss
    # recompiles from scratch. Caught in a real-LLM e2e run: a single stall of
    # 1027ms inside `_extend_values_for_multiparams`, pure CPU on the event
    # loop, in this transaction, with the row locks from the write that
    # produced these events still held.
    #
    # Passing the rows as executemany parameters compiles one statement
    # regardless of batch size, so it is a cache hit from the second call on and
    # the driver does the batching.
    await session.execute(
        insert(DomainEventOutbox).on_conflict_do_nothing(index_elements=["id"]),
        rows,
    )
