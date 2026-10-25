"""The brief's section naming the other pods the assistant can ask.

Without it the assistant only learned another pod existed if somebody named it:
asked about an invoice, it had no idea there was a Billing to ask. It is the
same list `list_teammates` reads (``pod_ask_queries.askable_pods``), narrowed the
same way (``narrowed``), so nothing is named here that the tool would refuse.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.services.context_brief.agent_self import READ_FAILED
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pod_ask_rules import AskingRun, OpenModes, open_modes
from app.modules.agent.infrastructure.pod_ask_queries import Teammate
from app.modules.agent.services.surface_context import surface_conversation_kind
from app.modules.agent.infrastructure.context_brief_repository import RepositoryFactory

logger = get_logger(__name__)


async def askable_pod_lines(
    uow_factory: UnitOfWorkFactory,
    repository: RepositoryFactory,
    *,
    conversation: Conversation,
    pod_id: UUID,
    user_id: UUID,
) -> list[str]:
    """The section, or nothing when this conversation can ask no other pod.

    Only the pods, and only the ways, that ``ask_teammate`` would allow from
    this conversation (``pod_asks.open_modes``): a scheduled run is not offered
    pods it could only ask as a person nobody is, and a channel is offered none.

    Best-effort like the people section: a failed read costs the brief this
    section, never the run its brief.
    """
    modes = open_modes(
        AskingRun(
            user_id=user_id,
            pod_id=pod_id,
            conversation=conversation,
            is_pod_default_agent=True,
            answers_outsider=False,
            surface_conversation_kind=surface_conversation_kind(conversation),
        )
    )
    if not (modes.as_person or modes.over_link):
        return []
    try:
        async with uow_factory() as uow:
            teammates = await repository(uow).list_askable_pods(
                pod_id=pod_id, user_id=user_id
            )
    except READ_FAILED:
        logger.warning(
            "agent.context_brief.askable_pods_unavailable.degraded",
            pod_id=str(pod_id),
            exc_info=True,
        )
        return []
    reachable = [one for one in (narrowed(t, modes) for t in teammates) if one]
    if not reachable:
        return []
    return [
        "\n## Other pods you can ask",
        (
            "- When the work is one of theirs, ask it with `ask_teammate`. "
            "[through the person]: it answers with the person's access there. "
            "[connected]: it answers with what it shared with this pod, also "
            "when nobody is here."
        ),
        *(
            f"- {one.name}"
            + (f" — {one.description}" if one.description else "")
            + f" [{', '.join(one.reach)}] (teammate: {one.pod_id})"
            for one in reachable
        ),
    ]


def narrowed(teammate: Teammate, modes: OpenModes) -> Teammate | None:
    """``teammate`` as this conversation can reach it, or None if it can't."""
    through_you = teammate.through_you and modes.as_person
    connected = teammate.connected and modes.over_link
    if not (through_you or connected):
        return None
    return replace(teammate, through_you=through_you, connected=connected)
