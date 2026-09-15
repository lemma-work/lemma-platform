"""The ``## You`` half of the runtime brief: what this agent is.

Split out of ``agent_context_brief`` for the same reason ``agent_memory_brief``
is -- it answers a different question from a different set of reads -- and kept
here rather than inline because it is the half that grew the file past what the
architecture gate allows.

Everything it renders already existed and none of it reached the prompt. The
agent did not know its own name: the pod's own agent is stored as
``pod_default`` and shown to people as ``Lem``, and neither string was in front
of it. It did not know what it was allowed to do without asking, because
``allowed_actions`` was authorization data and nothing else. It did not know its
standing work, though a schedule is the closest thing an agent has to a job and
its profile page has listed them under that heading for a while. And it knew
only the one channel the current run arrived on, so it could not tell anybody
where else to reach it.

Every read here is best-effort. A brief that cannot describe the agent is still
worth rendering, and a self-description is never worth failing somebody's run
over.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.delegation import agent_display_name
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.entities import Agent
from app.modules.agent.infrastructure.context_brief_repository import (
    AgentContextBriefRepository,
)
from app.modules.agent_surfaces.contracts.pod_summaries import PodSurfaceSummary
from app.modules.apps.contracts.pod_summaries import PodAppSummary
from app.modules.pod.contracts.members import PodProfile
from app.modules.schedule.contracts.pod_summaries import PodScheduleSummary

logger = get_logger(__name__)

# Smaller than the inventory caps on purpose: an agent needs to know it has
# standing work and roughly what it is, and the full list of forty schedules is
# one tool call away. A pod with three schedules and two apps -- which is most
# of them -- renders whole either way.
MAX_SCHEDULES = 12
MAX_APPS = 12
MAX_SURFACES = 8
MAX_PERMITS = 50

#: A database read that failed. Narrow rather than bare ``Exception`` so a bug
#: in the rendering below still surfaces instead of reading as "the pod has no
#: schedules": ``OSError`` because a connection can be refused before SQLAlchemy
#: has anything to say about it.
READ_FAILED = (SQLAlchemyError, OSError)


def schedule_line(summary: PodScheduleSummary) -> str:
    """One standing job, said the way a person would say it.

    The cadence comes out of the type-specific config, because "every weekday at
    nine" and "whenever a row lands in `tickets`" are the same kind of fact to
    whoever reads this and are stored in two different shapes.
    """
    name = summary.name or "(unnamed schedule)"
    config = summary.config or {}
    if summary.schedule_type == "TIME":
        cron = config.get("cron")
        at = config.get("scheduled_at")
        zone = config.get("timezone")
        when = f"cron `{cron}`" if cron else (f"once at {at}" if at else "on a timer")
        if zone:
            when += f" ({zone})"
    elif summary.schedule_type == "DATASTORE":
        table = config.get("table_name") or "a table"
        operations = config.get("operations") or []
        verbs = ", ".join(str(op).lower() for op in operations) or "any change"
        when = f"on {verbs} in `{table}`"
    else:
        when = "on an event from a connected app"
    line = f"- {name} — {when}"
    if not summary.is_active:
        line += " (paused)"
    instruction = (summary.instruction or "").strip()
    if instruction:
        line += f": {instruction[:160]}"
    return line


def _identity_lines(*, agent: Agent, pod: PodProfile, is_default: bool) -> list[str]:
    """Name, description, tenure and permits -- everything needing no read."""
    name = agent_display_name(agent.name) if is_default else (agent.name or "(unnamed)")
    lines = ["\n## You"]
    if is_default:
        lines.append(
            f"- You are **{name}**, this pod's own teammate: the one that "
            "answers here unless somebody names another agent."
        )
    else:
        lines.append(
            f"- You are **{name}**, one of this pod's named agents, built for "
            "the job in your instructions below."
        )
    if agent.description and agent.description.strip():
        lines.append(f"- How you are described: {agent.description.strip()}")
    if is_default and pod.created_at is not None:
        lines.append(f"- Here since {pod.created_at.date().isoformat()}.")
    if agent.allowed_actions:
        shown = sorted(agent.allowed_actions)[:MAX_PERMITS]
        extra = len(agent.allowed_actions) - len(shown)
        lines.append(
            "- What you may do without stopping to ask: "
            + ", ".join(shown)
            + (f" (+{extra} more)" if extra > 0 else "")
        )
    return lines


def _standing_work_lines(
    schedules: list[PodScheduleSummary], *, agent: Agent, is_default: bool
) -> list[str]:
    mine = [s for s in schedules if s.agent_id == agent.id]
    lines: list[str] = []
    if mine:
        lines.append("- Standing work wired to you:")
        lines.extend(f"  {schedule_line(s)}" for s in mine)
    others = [s for s in schedules if s.agent_id != agent.id]
    if others and is_default:
        # The teammate is the one asked "what runs around here", so it is told
        # about the schedules pointing elsewhere too -- named as somebody
        # else's, not as its own.
        lines.append("- Standing work in this pod, wired to something else:")
        lines.extend(f"  {schedule_line(s)}" for s in others)
    return lines


def _reach_line(surfaces: list[PodSurfaceSummary], *, agent: Agent) -> list[str]:
    reachable = [s for s in surfaces if s.agent_id == agent.id]
    if not reachable:
        return []
    return [
        "- People reach you on: "
        + ", ".join(
            s.platform.lower() + (f" ({s.handle})" if s.handle else "")
            for s in reachable
        )
        + ". Those are the same conversations you answer in here."
    ]


def _apps_line(apps: list[PodAppSummary]) -> list[str]:
    if not apps:
        return []
    shown = apps[:MAX_APPS]
    extra = len(apps) - len(shown)
    return [
        "- Apps running in this pod: "
        + ", ".join(
            app.name + (f" [{app.status.lower()}]" if app.status else "")
            for app in shown
        )
        + (f" (+{extra} more)" if extra > 0 else "")
        + ". `lemma pods describe` does not list apps; `lemma apps list` does."
    ]


class AgentSelfBriefBuilder:
    """Renders ``## You``, from the same reads the profile page already makes."""

    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def build(
        self, *, agent: Agent, pod: PodProfile, pod_id: UUID, is_default: bool
    ) -> list[str]:
        lines = _identity_lines(agent=agent, pod=pod, is_default=is_default)
        try:
            async with self.uow_factory() as uow:
                repo = AgentContextBriefRepository(uow)
                schedules, schedule_total = await repo.list_schedules(
                    pod_id=pod_id, limit=MAX_SCHEDULES
                )
                surfaces = await repo.list_surfaces(pod_id=pod_id, limit=MAX_SURFACES)
                apps = await repo.list_apps(pod_id=pod_id) if is_default else []
        except READ_FAILED:
            logger.warning(
                "agent.self_brief.reads_unavailable.degraded",
                pod_id=str(pod_id),
                exc_info=True,
            )
            return lines

        lines.extend(
            _standing_work_lines(schedules, agent=agent, is_default=is_default)
        )
        if schedule_total > len(schedules):
            lines.append(
                f"- … and {schedule_total - len(schedules)} more schedules not "
                "listed here. Use your tools to list them all."
            )
        lines.extend(_reach_line(surfaces, agent=agent))
        lines.extend(_apps_line(apps))
        return lines
