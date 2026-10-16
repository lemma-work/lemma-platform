"""A wait's work item: the one thing that fires it.

Kept apart from the service that resolves waits so the places that only arm or
disarm one -- ``wait_for`` writing the row, a stop or a new message cancelling
it -- do not import the resolution machinery (and, through it, back into the
tool package they live in).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.work import WorkWriter, pod_namespace
from app.modules.agent.domain.wait import AgentConversationWaitEntity, AgentWaitType
from app.modules.agent.events.work_kinds import (
    AGENT_WAIT_FIRE,
    AGENT_WAIT_POLL,
    wait_key,
)


class WaitTimer(BaseModel):
    """The payload of a wait's work item: which wait."""

    wait_id: UUID


def timer_kind(wait_type: AgentWaitType) -> str:
    """The kind of item a wait of ``wait_type`` is driven by."""
    return AGENT_WAIT_POLL if wait_type is AgentWaitType.PROCESS else AGENT_WAIT_FIRE


async def arm_wait(
    work: WorkWriter, wait: AgentConversationWaitEntity, at: datetime | None
) -> None:
    """Write the wait's item, due ``at`` (now when None). Coalesces a queued one."""
    await work.add(
        timer_kind(wait.wait_type),
        pod_namespace(wait.pod_id),
        WaitTimer(wait_id=wait.id),
        dedup_key=wait_key(wait.id),
        run_at=at,
    )


async def disarm_wait(work: WorkWriter, wait: AgentConversationWaitEntity) -> None:
    """Drop the wait's queued item, in the caller's transaction.

    Both kinds: the key is the wait's, whichever kind drives it. A running item
    is left alone (``WorkWriter.cancel``) and finds the wait ended when it
    takes the row lock.
    """
    ns = pod_namespace(wait.pod_id)
    for kind in (AGENT_WAIT_FIRE, AGENT_WAIT_POLL):
        await work.cancel(kind, ns, wait_key(wait.id))


__all__ = ["WaitTimer", "arm_wait", "disarm_wait", "timer_kind"]
