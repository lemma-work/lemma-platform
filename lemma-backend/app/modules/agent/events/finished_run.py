"""What a conversation is owed the moment one of its runs finishes.

Each of these is somebody waiting on the run that just ended, and each is the
module's own business; this only keeps them in one place, in order, so the event
handler that receives ``AgentRunCompletedEvent`` reads as one step.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.events import AgentRunCompletedEvent
from app.modules.agent.events.pod_ask_answers import deliver_pod_ask_answers
from app.modules.agent.events.queued_followup import (
    start_followup_run_for_queued_messages,
)
from app.modules.agent.events.subagent_waits import (
    resolve_parent_wait_for_finished_child,
)


async def settle_finished_run(
    event: AgentRunCompletedEvent, *, uow_factory: UnitOfWorkFactory
) -> None:
    # Anything the person sent while that run was busy has been sitting
    # unanswered: the run it joined had already read its history.
    await start_followup_run_for_queued_messages(event, uow_factory=uow_factory)
    # And a parent suspended on this child gets its turn back now, rather
    # than at the deadline its timer is holding as a backstop.
    await resolve_parent_wait_for_finished_child(event, uow_factory=uow_factory)
    # An answer another pod asked for, or one this conversation is owed.
    await deliver_pod_ask_answers(event, uow_factory=uow_factory)
