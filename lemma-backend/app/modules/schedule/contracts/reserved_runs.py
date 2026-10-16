"""A reserved schedule run, as the module that starts its target reads it.

``schedule.start_target`` hands a workflow target to the workflow module as a
``workflow.start`` item carrying only the run's id (payloads carry ids, not
snapshots). The workflow module reads the fire back through this, then claims
the same reservation through ``dispatch.claim_schedule_run`` as any start does.

A submodule rather than ``contracts/__init__`` for the reason its siblings
give: it reaches the model layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import JsonValue

from app.modules.schedule.infrastructure.models.run import ScheduleRun


@dataclass(frozen=True, slots=True)
class ReservedScheduleRun:
    schedule_run_id: UUID
    schedule_id: UUID
    user_id: UUID | None
    source_event_id: str
    payload: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    llm_output: dict[str, JsonValue]
    source_occurred_at: datetime | None


async def load_reserved_run(uow, schedule_run_id: UUID) -> ReservedScheduleRun | None:
    """The fire behind ``schedule_run_id``, or ``None`` if it no longer exists."""
    run = await uow.session.get(ScheduleRun, schedule_run_id)
    if run is None:
        return None
    return ReservedScheduleRun(
        schedule_run_id=run.id,
        schedule_id=run.schedule_id,
        user_id=run.user_id,
        source_event_id=run.source_event_id,
        payload=dict(run.payload or {}),
        metadata=dict(run.fire_metadata or {}),
        llm_output=dict(run.llm_output or {}),
        source_occurred_at=run.source_occurred_at,
    )


__all__ = ["ReservedScheduleRun", "load_reserved_run"]
