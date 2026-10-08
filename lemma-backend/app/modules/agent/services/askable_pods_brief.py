"""The brief's section naming the other pods the assistant can ask.

Without it the assistant only learned another pod existed if somebody named it:
asked about an invoice, it had no idea there was a Billing to ask. It is the
same list `list_teammates` reads (``pod_ask_queries.askable_pods``), so nothing
is named here that the tool would refuse.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.services.agent_self_brief import READ_FAILED
from app.modules.agent.infrastructure.pod_ask_queries import Teammate
from app.modules.agent.services.brief_seams import RepositoryFactory

logger = get_logger(__name__)


async def askable_pod_lines(
    uow_factory: UnitOfWorkFactory,
    repository: RepositoryFactory,
    *,
    pod_id: UUID,
    user_id: UUID,
) -> list[str]:
    """The section, or nothing when the person is in no other pod.

    Best-effort like the people section: a failed read costs the brief this
    section, never the run its brief.
    """
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
    if not teammates:
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
            + f" [{_reach(one)}] (teammate: {one.pod_id})"
            for one in teammates
        ),
    ]


def _reach(one: Teammate) -> str:
    return ", ".join(
        word
        for word, reachable in (
            ("through the person", one.through_you),
            ("connected", one.connected),
        )
        if reachable
    )
