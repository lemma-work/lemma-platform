"""What another module may know about a pod's schedules, as a listing.

A schedule is the closest thing an agent has to a standing job: something it
does without being asked each time. The agent's own profile page has listed them
under that heading for a while; its prompt never did, so an agent could be woken
every weekday at nine by a schedule it was unable to name.

Internal schedules are excluded. Those are created by workflow execution for
waits and timeouts -- machinery, not standing work, and listing them buries the
two or three entries that mean something under a dozen that do not.

Target ids rather than target names: the caller is the runtime brief, which
knows which agent it is rendering for and wants to say "this one is yours"
rather than repeat a name the reader already has. Resolving names here would
also mean eager-loading two relationships to answer a question nobody asked.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select

from app.modules.schedule.infrastructure.models.schedule import Schedule


@dataclass(frozen=True, slots=True)
class PodScheduleSummary:
    name: str | None
    #: TIME, DATASTORE or WEBHOOK -- a clock, a row changing, or the outside.
    schedule_type: str
    #: What the target is told to do when this fires, in the author's words.
    instruction: str | None
    agent_id: UUID | None
    workflow_id: UUID | None
    is_active: bool
    #: Type-specific: a cron expression, a watched table, a connector trigger.
    config: dict[str, object]


async def list_schedule_summaries(
    *, session, pod_id: UUID, limit: int
) -> tuple[list[PodScheduleSummary], int]:
    """This pod's non-internal schedules, plus the total that matched."""
    where = (Schedule.pod_id == pod_id, Schedule.is_internal.is_(False))
    total = (
        await session.execute(select(func.count()).select_from(Schedule).where(*where))
    ).scalar_one()
    rows = (
        await session.execute(
            select(
                Schedule.name,
                Schedule.schedule_type,
                Schedule.instruction,
                Schedule.agent_id,
                Schedule.workflow_id,
                Schedule.is_active,
                Schedule.config,
            )
            .where(*where)
            .order_by(Schedule.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        PodScheduleSummary(
            name=name,
            schedule_type=getattr(schedule_type, "value", str(schedule_type)),
            instruction=instruction,
            agent_id=agent_id,
            workflow_id=workflow_id,
            is_active=is_active,
            config=config or {},
        )
        for name, schedule_type, instruction, agent_id, workflow_id, is_active, config in rows
    ], int(total)
