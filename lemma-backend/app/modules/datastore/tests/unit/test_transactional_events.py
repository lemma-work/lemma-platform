from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.datastore.domain.events import DatastoreFileCreatedEvent
from app.modules.datastore.infrastructure.transactional_events import (
    stage_domain_events,
)


@pytest.mark.asyncio
async def test_stage_domain_events_uses_one_bulk_insert() -> None:
    session = AsyncMock()
    events = [
        DatastoreFileCreatedEvent(
            file_id=uuid4(),
            pod_id=uuid4(),
            path=f"/document-{index}.md",
        )
        for index in range(3)
    ]

    await stage_domain_events(session, events)

    session.execute.assert_awaited_once()
    rows = session.execute.await_args.args[1]
    assert {row["id"] for row in rows} == {event.event_id for event in events}


@pytest.mark.asyncio
async def test_the_outbox_statement_does_not_depend_on_the_batch_size() -> None:
    """One compiled statement for every batch size.

    Rendering the rows inline with ``.values(list)`` makes the SQL a function of
    how many rows there are, so the statement cache misses on every batch size
    it has not seen and recompiles from scratch. That cost lands on the event
    loop inside the write transaction, holding the row locks of the write that
    produced the events -- measured once at 1027ms.

    Asserting the compiled SQL is identical across batch sizes pins the
    property rather than the spelling: any future rewrite is free, as long as it
    does not put the row count back into the statement.
    """

    async def compile_for(count: int) -> str:
        session = AsyncMock()
        await stage_domain_events(
            session,
            [
                DatastoreFileCreatedEvent(
                    file_id=uuid4(), pod_id=uuid4(), path=f"/d-{index}.md"
                )
                for index in range(count)
            ],
        )
        return str(session.execute.await_args.args[0])

    assert await compile_for(1) == await compile_for(50)


@pytest.mark.asyncio
async def test_stage_domain_events_skips_empty_batches() -> None:
    session = AsyncMock()

    await stage_domain_events(session, [])

    session.execute.assert_not_awaited()
