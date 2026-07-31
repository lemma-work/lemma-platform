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
    statement = session.execute.await_args.args[0]
    parameters = statement.compile().params
    assert {value for key, value in parameters.items() if key.startswith("id_m")} == {
        event.event_id for event in events
    }


@pytest.mark.asyncio
async def test_stage_domain_events_skips_empty_batches() -> None:
    session = AsyncMock()

    await stage_domain_events(session, [])

    session.execute.assert_not_awaited()
