"""A worker that claims one schedule item and hangs inside it, to be SIGKILLed.

``python -m app.modules.schedule.tests.e2e.trigger_crash_entrypoint <stage> <marker> <calls>``

* ``fire``: inside ``schedule.fire``'s transaction, after the run is reserved
  and the start item written, before the commit;
* ``start``: inside ``schedule.start_target``'s completion transaction, after
  the agent run is created and the schedule run marked dispatched;
* ``filter``: inside the model call, after the FILTERING claim committed --
  the call is counted in ``<calls>``.

The marker file appears once the hang is reached; the test then kills the
process, and Postgres rolls back whatever was uncommitted.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import app.events  # noqa: F401  every module's kinds, as the worker imports them
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import dispatcher_for
from app.modules.schedule.domain.interfaces import FilterVerdict
from app.modules.schedule.services import target_start, trigger_fire
from app.modules.schedule.services.trigger_filter import (
    ScheduleFilter,
    install_schedule_filter,
)


async def _hang(marker: Path) -> None:
    marker.write_text("reached")
    await asyncio.Event().wait()


def _install(stage: str, marker: Path, calls: Path) -> WorkClass:
    if stage == "fire":
        original = trigger_fire.add_start_work

        async def enqueue_then_hang(*args, **kwargs) -> None:
            await original(*args, **kwargs)
            await _hang(marker)

        trigger_fire.add_start_work = enqueue_then_hang
        return WorkClass.EVENTS
    if stage == "start":
        repository = target_start.ScheduleRunRepository
        original_mark = repository.mark_dispatched

        async def mark_then_hang(self, run_id) -> None:
            await original_mark(self, run_id)
            await _hang(marker)

        repository.mark_dispatched = mark_then_hang
        return WorkClass.AUTOMATION

    class HangingModel(ScheduleFilter):
        async def judge(self, schedule, event) -> FilterVerdict:
            with calls.open("a") as handle:
                handle.write(f"{event.source_event_id}\n")
            await _hang(marker)
            raise AssertionError("unreachable")

    install_schedule_filter(HangingModel())
    return WorkClass.AUTOMATION


async def main(stage: str, marker: Path, calls: Path) -> None:
    cls = _install(stage, marker, calls)
    dispatcher = dispatcher_for(async_session_maker, cls, owner=f"crasher-{stage}")
    # The test makes the item due before starting this process.
    items = await dispatcher.claim_once()
    if not items:
        raise SystemExit("nothing to claim")
    await asyncio.gather(*(dispatcher.execute(item) for item in items))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])))
