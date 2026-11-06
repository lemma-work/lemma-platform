"""How often each schedule has fired lately, across many pods at once.

A pod's sidebar shows its own work as one line per automation: the schedule,
its newest fire and how many times it fired lately. One statement for every
pod given; each schedule's fires are read from ``ix_schedule_runs_schedule_fires``
inside the window, never its whole history.

Two filters, both load-bearing, and both the ones ``contracts.pod_summaries``
applies. Internal schedules are machinery -- workflow waits and timeouts --
and are left out. **Visibility is enforced, not assumed**: a schedule carries
its own visibility and owner, so each pod's rows go through
``allowed_actions_expr`` against that pod's own context. A fire the filter
skipped is not a fire.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Subquery, and_, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import Context, ResourceType
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.modules.schedule.domain.schedule import ScheduleRunStatus
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.infrastructure.models.schedule import Schedule


@dataclass(frozen=True, slots=True)
class ScheduleFireActivity:
    """One schedule's fires inside the window: how many, and the newest."""

    pod_id: UUID
    schedule_id: UUID
    schedule_name: str | None
    fire_count: int
    latest_fire_id: UUID
    #: The target's outcome once there is one, else where the fire stands.
    latest_status: str
    latest_fire_at: datetime
    #: AGENT or WORKFLOW: what ``latest_target_run_id`` names.
    latest_target_kind: str
    #: The conversation an AGENT target ran in, or a WORKFLOW target's run;
    #: None until the fire has reached its target.
    latest_target_run_id: str | None


def _readable_in_their_pods(contexts: Mapping[UUID, Context]) -> ColumnElement[bool]:
    """Schedules each pod's own viewer may read, as one WHERE-able expression."""
    return or_(
        *[
            and_(
                Schedule.pod_id == pod_id,
                allowed_actions_contains(
                    allowed_actions_expr(
                        ctx=ctx,
                        resource_type=ResourceType.SCHEDULE,
                        resource_id_col=Schedule.id,
                        pod_id_col=Schedule.pod_id,
                        owner_user_id_col=Schedule.user_id,
                        visibility_col=Schedule.visibility,
                    ),
                    Permissions.SCHEDULE_READ,
                ),
            )
            for pod_id, ctx in contexts.items()
        ]
    )


async def schedule_fire_activity_by_pod(
    session: AsyncSession,
    *,
    contexts: Mapping[UUID, Context],
    since: datetime,
    per_pod: int,
) -> dict[UUID, list[ScheduleFireActivity]]:
    """Each pod's schedules that fired since ``since``, the latest fire first.

    ``per_pod`` caps each pod rather than the whole answer. Pods with nothing
    are absent; read through ``.get(pod_id, [])``.
    """
    if not contexts:
        return {}
    latest = latest_fires_ranked_by_pod(contexts, since=since)
    rows = (
        await session.execute(
            select(latest)
            .where(latest.c.rank <= per_pod)
            .order_by(latest.c.pod_id, latest.c.created_at.desc(), latest.c.id.desc())
            # The rank already caps each pod; the ceiling on the whole read is
            # written out so it does not have to be reconstructed from it.
            .limit(per_pod * len(contexts))
        )
    ).all()
    by_pod: dict[UUID, list[ScheduleFireActivity]] = {}
    for row in rows:
        by_pod.setdefault(row.pod_id, []).append(
            ScheduleFireActivity(
                pod_id=row.pod_id,
                schedule_id=row.schedule_id,
                schedule_name=row.name,
                fire_count=int(row.fires),
                latest_fire_id=row.id,
                latest_status=str(row.status),
                latest_fire_at=row.created_at,
                latest_target_kind=str(row.target_kind),
                latest_target_run_id=row.target_run_id,
            )
        )
    return by_pod


def latest_fires_ranked_by_pod(
    contexts: Mapping[UUID, Context], *, since: datetime
) -> Subquery:
    """Each readable schedule's newest fire since ``since`` with its fire count,
    numbered within its pod by recency -- the statement
    ``schedule_fire_activity_by_pod`` caps."""
    fire = ScheduleRun
    recent = (
        select(
            Schedule.pod_id,
            Schedule.id.label("schedule_id"),
            Schedule.name,
            fire.id,
            func.coalesce(fire.target_outcome, fire.status).label("status"),
            fire.created_at,
            fire.target_kind,
            fire.target_run_id,
            func.row_number()
            .over(
                partition_by=fire.schedule_id,
                order_by=(fire.created_at.desc(), fire.id.desc()),
            )
            .label("newest"),
            func.count().over(partition_by=fire.schedule_id).label("fires"),
        )
        .join(Schedule, Schedule.id == fire.schedule_id)
        .where(
            Schedule.pod_id.in_(list(contexts)),
            Schedule.is_internal.is_(False),
            _readable_in_their_pods(contexts),
            # Rendered inline rather than bound: a planner can match the
            # partial ``ix_schedule_runs_schedule_fires`` to a constant, never to
            # a parameter of a generic plan.
            fire.status
            != literal(ScheduleRunStatus.FILTERED.value, literal_execute=True),
            fire.created_at >= since,
        )
        .subquery("recent")
    )
    return (
        select(
            *recent.c,
            func.row_number()
            .over(
                partition_by=recent.c.pod_id,
                order_by=(recent.c.created_at.desc(), recent.c.id.desc()),
            )
            .label("rank"),
        )
        .where(recent.c.newest == 1)
        .subquery("latest")
    )


__all__ = ["ScheduleFireActivity", "schedule_fire_activity_by_pod"]
