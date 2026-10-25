"""What moved lately in each of an organization's pods, for one member.

Per pod: the member's latest chats, the pod's automations that ran -- one line
per workflow or schedule, with its newest run and how many it ran -- and a
count per day of the member's conversations, split by whether an agent or a
person started them. A sidebar section and a board's two-week bars draw from
it.

It replaces about ten requests per pod, and fixes what they got wrong: the bars
were counted from each pod's first page of twenty-five conversations, so a busy
pod's fortnight looked like its last afternoon. Every read below is one
statement for all the pods together.

Chats and the day counts are the member's own conversations -- a conversation
is its starter's. Automations are the pod's: every run of a workflow or fire of
a schedule the member may read, whoever started it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.contracts.conversation_summaries import (
    DayActivity,
    RecentChat,
    daily_activity_by_pod,
    recent_chats_by_pod,
)
from app.modules.identity.domain.time_zones import last_days
from app.modules.identity.services.organization_scope import load_member_scope
from app.modules.schedule.contracts.fire_activity import (
    ScheduleFireActivity,
    schedule_fire_activity_by_pod,
)
from app.modules.workflow.contracts.run_activity import (
    WorkflowRunActivity,
    workflow_run_activity_by_pod,
)

#: The most automations one pod lists: a ceiling no ordinary pod reaches, so
#: that one pod with hundreds cannot make the read grow without bound.
AUTOMATIONS_PER_POD = 50

#: What a schedule's fire names when its target is a conversation or a run.
_AGENT_TARGET = "AGENT"
_WORKFLOW_TARGET = "WORKFLOW"


class AutomationKind(str, Enum):
    WORKFLOW = "WORKFLOW"
    SCHEDULE = "SCHEDULE"


@dataclass(frozen=True, slots=True)
class Automation:
    """A workflow or a schedule that ran inside the window."""

    kind: AutomationKind
    #: The workflow's or the schedule's id.
    id: UUID
    name: str | None
    #: Runs of the workflow, or fires of the schedule, inside the window.
    run_count: int
    #: The newest: a workflow run's id, or a schedule fire's.
    latest_run_id: UUID
    latest_status: str
    latest_at: datetime
    #: What to open for the newest: the workflow run (a workflow's own, or the
    #: one a schedule started) or the conversation a schedule's agent ran in.
    workflow_run_id: UUID | None
    conversation_id: UUID | None


@dataclass(frozen=True, slots=True)
class DayCount:
    day: date
    by_agent: int
    by_people: int


@dataclass(frozen=True, slots=True)
class PodRecents:
    pod_id: UUID
    pod_name: str
    chats: list[RecentChat]
    automations: list[Automation]
    #: One entry per day of the window, oldest first, zeros included.
    activity: list[DayCount]


@dataclass(frozen=True, slots=True)
class OrganizationRecents:
    organization_id: UUID
    days: list[date]
    since: datetime
    pods: list[PodRecents]


def _uuid(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


def workflow_automation(activity: WorkflowRunActivity) -> Automation:
    return Automation(
        kind=AutomationKind.WORKFLOW,
        id=activity.workflow_id,
        name=activity.workflow_name,
        run_count=activity.run_count,
        latest_run_id=activity.latest_run_id,
        latest_status=activity.latest_run_status,
        latest_at=activity.latest_run_at,
        workflow_run_id=activity.latest_run_id,
        conversation_id=None,
    )


def schedule_automation(activity: ScheduleFireActivity) -> Automation:
    """A schedule's line: its fires, and whatever its newest one started."""
    target = _uuid(activity.latest_target_run_id)
    return Automation(
        kind=AutomationKind.SCHEDULE,
        id=activity.schedule_id,
        name=activity.schedule_name,
        run_count=activity.fire_count,
        latest_run_id=activity.latest_fire_id,
        latest_status=activity.latest_status,
        latest_at=activity.latest_fire_at,
        workflow_run_id=(
            target if activity.latest_target_kind == _WORKFLOW_TARGET else None
        ),
        conversation_id=(
            target if activity.latest_target_kind == _AGENT_TARGET else None
        ),
    )


def newest_first(automations: list[Automation]) -> list[Automation]:
    return sorted(
        automations, key=lambda each: (each.latest_at, each.latest_run_id), reverse=True
    )


def filled_days(
    days: list[date], counted: list[DayActivity]
) -> dict[UUID, list[DayCount]]:
    """Each pod's counts on every day asked for, oldest first, zeros included:
    a day nothing happened on is a bar of nothing, not a gap in the chart."""
    by_pod: dict[UUID, dict[date, DayActivity]] = {}
    for each in counted:
        by_pod.setdefault(each.pod_id, {})[each.day] = each
    return {
        pod_id: [
            DayCount(
                day=day,
                by_agent=on[day].by_agent if day in on else 0,
                by_people=on[day].by_people if day in on else 0,
            )
            for day in days
        ]
        for pod_id, on in by_pod.items()
    }


async def load_recents(
    session: AsyncSession,
    *,
    user_id: UUID,
    organization_id: UUID,
    days: int,
    chats_per_pod: int,
    zone_name: str,
    now: datetime | None = None,
) -> OrganizationRecents | None:
    """Every pod's recents for the member. None when they are not a member.

    ``zone_name`` is an IANA name already checked by ``known_zone``; the days
    are its calendar days, and the window starts at the first one's midnight.
    Every pod the member may open is listed, quiet ones included, so a board
    can draw a lane for each.
    """
    scope = await load_member_scope(
        session=session, user_id=user_id, organization_id=organization_id
    )
    if scope is None:
        return None
    window, since = last_days(
        now=now or datetime.now(timezone.utc), zone=ZoneInfo(zone_name), days=days
    )
    chats = await recent_chats_by_pod(
        session, user_id=user_id, pod_ids=scope.pod_ids, per_pod=chats_per_pod
    )
    counted = await daily_activity_by_pod(
        session, user_id=user_id, pod_ids=scope.pod_ids, since=since, zone=zone_name
    )
    workflows = await workflow_run_activity_by_pod(
        session, contexts=scope.contexts, since=since, per_pod=AUTOMATIONS_PER_POD
    )
    schedules = await schedule_fire_activity_by_pod(
        session, contexts=scope.contexts, since=since, per_pod=AUTOMATIONS_PER_POD
    )
    activity = filled_days(window, counted)
    quiet = [DayCount(day=day, by_agent=0, by_people=0) for day in window]
    return OrganizationRecents(
        organization_id=organization_id,
        days=window,
        since=since,
        pods=[
            PodRecents(
                pod_id=pod.id,
                pod_name=pod.name,
                chats=chats.get(pod.id, []),
                automations=newest_first(
                    [
                        *map(workflow_automation, workflows.get(pod.id, [])),
                        *map(schedule_automation, schedules.get(pod.id, [])),
                    ]
                )[:AUTOMATIONS_PER_POD],
                activity=activity.get(pod.id, quiet),
            )
            for pod in scope.pods
        ],
    )
