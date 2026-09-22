"""What the datastore consumer refuses to pay for.

This is the noisiest stream on the platform -- every row written anywhere lands
on it -- and almost nothing on it is for a pod that has a DATASTORE schedule.
The consumer used to claim the durable inbox first and find that out second, so
~97% of record events each bought a Postgres row that outlived the event, a
claim transaction, a jsonb match query and a completion transaction, to decide
there was nothing to do.

In production that consumer fell minutes behind one tenant's bulk import, its
pending list passed 28,000, and a pending list is what stops a stream being
trimmed -- so entries aged out undelivered and other tenants' triggers never
fired.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import partial
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.schedule.handlers import datastore_consumer


@asynccontextmanager
async def _mock_uow_factory(uow_mock):
    yield uow_mock


class _Inbox:
    """Records whether the durable claim was reached at all.

    Deliberately does not run the handler: what these tests are about is which
    events get as far as costing a row. That the dispatch itself fires the right
    schedules is ``test_datastore_event_handler``'s subject, and re-asserting it
    here would mean standing a double in front of the handler to do it.
    """

    def __init__(self) -> None:
        self.claims = 0

    async def process(self, consumer, event, handler):
        assert consumer == "schedule-datastore-events"
        del event, handler
        self.claims += 1
        return True


def _watch_lookup(answer: bool, asked: list[object]):
    async def pod_is_watched(pod_id):
        asked.append(pod_id)
        return answer

    return pod_is_watched


def _record_event(pod_id) -> dict:
    return {
        "event_type": "datastore.record.insert",
        "pod_id": str(pod_id),
        "table_name": "pings",
        "record_id": str(uuid4()),
        "operation": "INSERT",
        "payload": {},
    }


async def _consume(event, *, watched: bool, asked: list[object], inbox: _Inbox):
    await datastore_consumer.handle_datastore_event(
        event,
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, AsyncMock()),
        inbox=inbox,
        pod_is_watched=_watch_lookup(watched, asked),
    )


@pytest.mark.asyncio
async def test_a_pod_nobody_is_watching_never_reaches_the_inbox():
    """The 97% case, and the whole point of the change.

    No durable row, no claim transaction, no match query -- the event is
    acknowledged and forgotten. Safe because the inbox makes *side effects*
    exactly-once, and a pod with no DATASTORE schedule has no side effect to
    protect: doing nothing twice is still doing nothing.
    """
    asked: list[object] = []
    inbox = _Inbox()
    pod_id = uuid4()

    await _consume(_record_event(pod_id), watched=False, asked=asked, inbox=inbox)

    assert [str(p) for p in asked] == [str(pod_id)]
    assert inbox.claims == 0, "a pod nobody watches must not cost a durable row"


@pytest.mark.asyncio
async def test_a_watched_pod_still_reaches_the_inbox():
    """The 3% case is unchanged: the cheap filter must not become the feature.

    A pod somebody is watching still goes through the durable claim, so firing
    keeps the exactly-once guarantee it had before.
    """
    asked: list[object] = []
    inbox = _Inbox()

    await _consume(_record_event(uuid4()), watched=True, asked=asked, inbox=inbox)

    assert len(asked) == 1
    assert inbox.claims == 1


@pytest.mark.asyncio
async def test_a_non_record_event_costs_nothing_either():
    """The stream also carries datastore/table/file events.

    Checked before the pod lookup, so the cheapest rejection stays cheapest --
    the lookup is never even asked.
    """
    asked: list[object] = []
    inbox = _Inbox()

    await _consume(
        {"event_type": "datastore.file.updated", "pod_id": str(uuid4())},
        watched=True,
        asked=asked,
        inbox=inbox,
    )

    assert asked == []
    assert inbox.claims == 0
