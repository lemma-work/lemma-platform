"""What became of an automated run, and stopping one nobody will read.

A workflow step waits on its conversation: it reads the status and output of
the latest run, and when the workflow is cancelled it stops the run that is
still going. Either is the engine acting on a run it already authorized, so
neither checks access.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.automation_prompts import step_output
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.value_objects import ConversationStatus
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.run_stop import stop_run


class AutomatedRunOutcome:
    """Reads, and stops, the run a workflow step is waiting on."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.runs = Runs(uow)
        self.pauses = Pauses(uow)

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> AutomatedRunOutcome:
        return cls(uow)

    async def status(self, conversation_id: UUID) -> dict[str, object]:
        """``status`` -- ``COMPLETED``, ``WAITING``, ``FAILED``, ``RUNNING`` or
        ``NOT_FOUND`` -- with the output, and why it waits or failed."""
        # With its latest run: `last_run_error` is derived from it, and without
        # it a failed step only ever said that the agent failed.
        conversation = await self.conversations.get_conversation(
            conversation_id, include_runs=True
        )
        if conversation is None or conversation.status is None:
            return {"status": "NOT_FOUND"}
        output = step_output(conversation.output)
        if conversation.status is ConversationStatus.COMPLETED:
            return {"status": "COMPLETED", "output_data": output}
        if conversation.status is ConversationStatus.WAITING:
            # The expiry policy needs to know *why*: an agent blocked on a
            # person is the hang the ceiling exists to catch, while a waiting
            # agent wakes itself (`run_resume_service._expire_overdue_wait`).
            waits = await self.pauses.pending(conversation_id, kinds=(PauseKind.WAIT,))
            wakes_at = waits[0].wake_at if waits else None
            return {
                "status": "WAITING",
                "wait_reason": "WAIT" if waits else "HUMAN",
                "wakes_at": wakes_at.isoformat() if wakes_at else None,
                "output_data": output,
            }
        if conversation.status in {
            ConversationStatus.FAILED,
            ConversationStatus.STOPPED,
        }:
            # The run's own error, not just the status: the reason is what the
            # agent wrote to `last_run_error`.
            reason = (conversation.last_run_error or "").strip()
            label = f"Agent conversation {conversation.status.value}"
            return {
                "status": "FAILED",
                "error": f"{label}: {reason}" if reason else label,
                "output_data": output,
            }
        return {"status": "RUNNING"}

    async def stop(self, conversation_id: UUID) -> None:
        """Ask the conversation's active run to stop. Nothing to stop is
        success: the agent may have finished between the cancel and this."""
        active_run = await self.runs.get_active_agent_run_for_update(conversation_id)
        if active_run is None:
            return
        conversation = await self.conversations.get_conversation(conversation_id)
        if conversation is None:
            return
        await stop_run(
            self.uow,
            self.runs,
            pod_id=conversation.pod_id,
            conversation_id=conversation_id,
            run_id=active_run.id,
        )
