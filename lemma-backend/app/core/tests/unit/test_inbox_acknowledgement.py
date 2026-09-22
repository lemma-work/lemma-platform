"""Which deliveries the inbox acknowledges, and which it hands back.

Redis Streams have no timeout: an entry leaves the pending-entries list when it
is XACKed and at no other moment. So for a delivery the inbox declines to run,
"return quietly" and "hand back" are not two styles of the same thing -- the
first destroys the only record that the work is still owed.

The inbox used to return quietly for three different situations at once. Two of
them are finished work and acknowledging them is right. The third is "another
worker holds this claim", and acknowledging *that* is how an event disappears:
if the holder then dies (OOM, eviction, a rolling deploy), its row stays
PROCESSING forever and the stream entry is already gone.

Seen in production on 2026-09-21 as ~28,500 pending entries against rows stuck
in PROCESSING since July, while the triggers those events should have fired
never fired.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from faststream.exceptions import NackMessage

from app.core.infrastructure.events.inbox import (
    ClaimOutcome,
    InboxConsumer,
    InboxStatus,
)


class _Claiming(InboxConsumer):
    """An inbox whose claim answer is dictated, so `process` can be read alone."""

    def __init__(self, outcome) -> None:
        super().__init__(AsyncMock())  # type: ignore[arg-type]
        self._outcome = outcome
        self.finished: list[InboxStatus] = []

    async def _claim(self, consumer, event_id, event_type):
        return self._outcome

    async def _finish(self, consumer, event_id, status, **kwargs) -> None:
        del kwargs
        self.finished.append(status)


def _event() -> dict:
    return {"event_type": "test.created", "event_id": str(uuid4())}


@pytest.mark.asyncio
async def test_an_event_someone_already_settled_is_acknowledged():
    """Returning normally is what makes FastStream acknowledge.

    Right here: the work is done, and redelivering would only reach the same
    answer. This is the case the old single return value got correct.
    """
    inbox = _Claiming(ClaimOutcome.ALREADY_SETTLED)
    ran = False

    async def handler() -> None:
        nonlocal ran
        ran = True

    assert await inbox.process("worker", _event(), handler) is False
    assert ran is False
    assert inbox.finished == []


@pytest.mark.asyncio
async def test_a_delivery_another_worker_holds_is_handed_back_not_acknowledged():
    """The regression. `NackMessage` is FastStream's "do not acknowledge".

    On a Redis stream its nack is precisely a no-op -- no XACK -- so the entry
    stays pending and the reclaim subscriber can offer it again once the
    holder's claim has aged out. Raising is the only way to get that: any
    ordinary return acknowledges.
    """
    inbox = _Claiming(ClaimOutcome.IN_FLIGHT_ELSEWHERE)
    ran = False

    async def handler() -> None:
        nonlocal ran
        ran = True

    with pytest.raises(NackMessage):
        await inbox.process("worker", _event(), handler)

    # Handed back, not run twice, and not recorded as an outcome it did not have.
    assert ran is False
    assert inbox.finished == []


@pytest.mark.asyncio
async def test_a_claimable_delivery_still_runs_and_completes():
    """The ordinary path is untouched by the distinction."""
    inbox = _Claiming(1)
    ran = False

    async def handler() -> None:
        nonlocal ran
        ran = True

    assert await inbox.process("worker", _event(), handler) is True
    assert ran is True
    assert inbox.finished == [InboxStatus.COMPLETED]


@pytest.mark.asyncio
async def test_holding_a_delivery_does_not_refresh_the_holder_s_claim():
    """Why handing back cannot livelock, asserted rather than argued.

    Each hand-back leaves `last_received_at` where the holder set it, so the
    claim keeps ageing. The reclaim subscriber's idle threshold is the same
    `abandon_after` window, so by the next redelivery the claim has expired and
    the branch below it re-claims instead. A hand-back that refreshed the
    timestamp would renew the very lease it is waiting on, and the message
    would bounce for as long as the deployment lived.
    """
    now = datetime.now(timezone.utc)
    claimed_at = now - timedelta(seconds=5)
    row = SimpleNamespace(
        status=InboxStatus.PROCESSING.value,
        attempts=1,
        delivery_count=1,
        last_received_at=claimed_at,
        last_error_type=None,
        last_error=None,
    )

    inbox = InboxConsumer(_session_maker(_Session(row)), abandon_after_seconds=60)
    assert (
        await inbox._claim("worker", uuid4(), "test.created")
        is ClaimOutcome.IN_FLIGHT_ELSEWHERE
    )

    assert row.last_received_at == claimed_at, "the hand-back renewed the lease"
    assert row.delivery_count == 1, "the hand-back counted itself as a delivery"

    # And once that same claim has aged past the window, the next delivery takes
    # it over rather than handing back again.
    row.last_received_at = now - timedelta(seconds=90)
    taken_over = InboxConsumer(_session_maker(_Session(row)), abandon_after_seconds=60)
    assert await taken_over._claim("worker", uuid4(), "test.created") == 2
    assert row.delivery_count == 2


class _Session:
    """The two statements `_claim` issues, with a dictated row for the second."""

    def __init__(self, row) -> None:
        self.row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        return self

    async def execute(self, statement, parameters=None):
        del statement, parameters

    async def scalar(self, statement):
        del statement
        return self.row


def _session_maker(session):
    def make():
        return session

    return make
