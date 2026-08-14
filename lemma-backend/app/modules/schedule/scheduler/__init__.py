"""Emitting a schedule fire.

What is left after APScheduler: the event a fire produces, and the dedup key it
carries. The scheduling itself -- deciding what is due, claiming it, and firing
exactly once across replicas -- lives in ``schedule.services`` and is driven by
the poller in the worker.
"""

from app.modules.schedule.scheduler.events import (
    SchedulerEventEmitter,
    get_event_emitter,
)

__all__ = [
    "SchedulerEventEmitter",
    "get_event_emitter",
]
