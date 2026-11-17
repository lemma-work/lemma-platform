"""A conversation parked WAITING keeps its machine slot; one that ends frees it."""

from __future__ import annotations

from uuid import uuid4

from app.modules.agent.services.run_finalizer import _end_machine_work
from app.modules.agent.domain.value_objects import ConversationStatus


async def test_waiting_parks_the_slot_and_ending_frees_it() -> None:
    calls: list[tuple[str, object]] = []

    async def park(run_id):
        calls.append(("park", run_id))

    async def end(run_id):
        calls.append(("end", run_id))
        return []

    waiting, finished = uuid4(), uuid4()

    await _end_machine_work(waiting, ConversationStatus.WAITING, park=park, end=end)
    await _end_machine_work(finished, ConversationStatus.COMPLETED, park=park, end=end)

    assert calls == [("park", waiting), ("end", finished)]
