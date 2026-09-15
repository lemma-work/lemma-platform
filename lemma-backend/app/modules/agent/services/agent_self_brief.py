"""The ``## You`` half of the runtime brief: what this agent is.

Split out of ``agent_context_brief`` for the same reason ``agent_memory_brief``
is -- it answers a different question from a different set of reads -- and kept
here rather than inline because it is the half that grew the file past what the
architecture gate allows.

Everything it renders already existed and none of it reached the prompt. The
agent did not know its own name -- which is the pod's name, because the pod is
the teammate; it is stored as ``pod_default`` and neither string was in front of
it. It did not know its standing work, though a schedule is the closest thing an
agent has to a job. And it knew only the one channel the current run arrived on,
so it could not tell anybody where else to reach it.

What this deliberately does *not* render is ``Agent.allowed_actions``. That
field reads like the agent's own authority and is the opposite: the repository
computes it for ``ResourceType.AGENT``, so it holds the *caller's* permitted
actions on the agent row -- read, execute, update, delete. Printing it as "what
you may do without asking" would tell an agent it may delete, meaning somebody
else may delete *it*. It is also empty on the ordinary path, because the
conversation resolver loads the agent without a context. What the agent may
actually do is its resource grants and the invoking user's permissions, which
the inventory and the pod section already cover.

Every read here is best-effort, and every one of them is filtered by the
invoking user's authorization context: schedules, workflows and apps each carry
their own visibility and owner, and a schedule's instruction is free text
somebody wrote. A brief that cannot describe the agent is still worth rendering,
and a self-description is never worth failing somebody's run over.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.factory import create_authorization_data_service
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
    """Name, description and tenure -- everything needing no read.

    The pod's own agent is named by the **pod**, not by
    ``DEFAULT_RESPONDER_NAME``. That constant is the platform's word for
    whatever answers in a pod, and it is the right answer to "who sent this
    Slack message" where no other name exists. It is the wrong answer here: a
    person with six teammates would be introduced to six agents all called Lem,
    which is precisely why the room app stopped rendering it and gives each pod
    its own name and face.
    """
    lines = ["\n## You"]
    if is_default and pod.name:
        lines.append(
            f"- You are **{pod.name}**. The pod is the teammate, so its name is "
            "your name and its work is your work. You answer here unless "
            "somebody names another agent."
        )
    elif is_default:
        # A pod row that no longer resolves. Say what is still true rather than
        # reaching for a placeholder name and asserting it.
        lines.append(
            "- You are this pod's own teammate: the one that answers here "
            "unless somebody names another agent."
        )
    else:
        lines.append(
            f"- You are **{agent.name or '(unnamed)'}**, one of this pod's "
            "named agents, built for the job in your instructions below."
        )
    if agent.description and agent.description.strip():
        lines.append(f"- How you are described: {agent.description.strip()}")
    if is_default and pod.created_at is not None:
        lines.append(f"- Here since {pod.created_at.date().isoformat()}.")
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

    # What an app is for, and where to open it -- not a status label on its own.
    # `status` is how the app is configured, not evidence that it works, and a
    # bare "[ready]" invites the agent to report it as working.
    def _one(app: PodAppSummary) -> str:
        line = f"  - {app.name}"
        if app.description:
            line += f" — {app.description}"
        if app.url:
            line += f" ({app.url})"
        return line

    lines = ["- Apps in this pod:", *(_one(app) for app in shown)]
    if extra > 0:
        lines.append(f"  - … and {extra} more")
    lines.append("  `lemma pods describe` does not list apps; `lemma apps list` does.")
    return lines


class AgentSelfBriefBuilder:
    """Renders ``## You``, from the same reads the profile page already makes."""

    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def build(
        self,
        *,
        agent: Agent,
        pod: PodProfile,
        pod_id: UUID,
        user_id: UUID,
        is_default: bool,
    ) -> list[str]:
        lines = _identity_lines(agent=agent, pod=pod, is_default=is_default)
        try:
            async with self.uow_factory() as uow:
                # The same authorization context every other read in the brief
                # builds. Schedules, workflows and apps each carry their own
                # visibility and owner, so pod membership does not entitle this
                # user to all of them -- and a schedule's instruction is free
                # text somebody wrote.
                ctx = await create_authorization_data_service(uow).build_user_context(
                    user_id=user_id, pod_id=pod_id
                )
                repo = AgentContextBriefRepository(uow)
                schedules, schedule_total = await repo.list_schedules(
                    pod_id=pod_id, ctx=ctx, limit=MAX_SCHEDULES
                )
                # Surfaces are pod-level channel configuration with no owner or
                # visibility of their own, so there is nothing to filter them by.
                surfaces = await repo.list_surfaces(pod_id=pod_id, limit=MAX_SURFACES)
                apps = (
                    await repo.list_apps(pod_id=pod_id, ctx=ctx) if is_default else []
                )
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
