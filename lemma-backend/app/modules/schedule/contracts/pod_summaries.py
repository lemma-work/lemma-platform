"""What another module may know about a pod's schedules, as a listing.

A schedule is the closest thing an agent has to a standing job: something it
does without being asked each time. The agent's own profile page has listed them
under that heading for a while; its prompt never did, so an agent could be woken
every weekday at nine by a schedule it was unable to name.

Two filters, and both are load-bearing.

Internal schedules are excluded because they are machinery -- created by
workflow execution for waits and timeouts -- and listing them buries the two or
three entries that mean something under a dozen that do not.

**Visibility is enforced, not assumed.** A schedule carries its own visibility
and owner, so membership in a pod does not entitle you to read every schedule in
it. This runs the same ``allowed_actions_expr`` filter the schedule repository's
own listings run, against the *invoking user's* context, and the count is taken
through the same filter as the rows -- a total computed over everything would
tell the reader how many schedules they cannot see. The first version of this
file selected on ``pod_id`` alone, which put another member's private schedule,
instruction text included, into this user's agent prompt.

Target ids rather than target names: the caller is the runtime brief, which
knows which agent it is rendering for and wants to say "this one is yours"
rather than repeat a name the reader already has. Resolving names here would
also mean eager-loading two relationships to answer a question nobody asked.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select

from app.core.authorization.context import Context, ResourceType
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
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


def _readable(ctx: Context):
    """The rows this context may read, as a WHERE-able expression."""
    return allowed_actions_contains(
        allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.SCHEDULE,
            resource_id_col=Schedule.id,
            pod_id_col=Schedule.pod_id,
            owner_user_id_col=Schedule.user_id,
            visibility_col=Schedule.visibility,
        ),
        Permissions.SCHEDULE_READ,
    )


async def list_schedule_summaries(
    *, session, pod_id: UUID, ctx: Context, limit: int
) -> tuple[list[PodScheduleSummary], int]:
    """The non-internal schedules this context may read, and how many there are.

    ``ctx`` is required rather than optional. An optional authorization context
    is one a caller forgets, and the thing it is protecting here is the free
    text somebody wrote into a private schedule's instruction.
    """
    where = (
        Schedule.pod_id == pod_id,
        Schedule.is_internal.is_(False),
        _readable(ctx),
    )
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
        for (
            name,
            schedule_type,
            instruction,
            agent_id,
            workflow_id,
            is_active,
            config,
        ) in rows
    ], int(total)
