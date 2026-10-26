"""Closing the pauses a person moved past without answering.

A new message, a follow-up turn and Stop all reach a conversation that may
still be sitting on an ``ask_user`` / ``request_approval`` nobody decided, or a
``wait_for`` still armed. Each closes them the same way -- a DENY recorded on
the person's behalf, a cancelled wait, and the returns the next run replays --
and that is this use case.

It is the one resolution nobody asked for: every other one records a decision a
person made. And it is the one use case that does not commit: its writes ride
the caller's transaction, so they land atomically with the run or the stop that
moved past the pause, and the caller publishes the returned messages once that
transaction commits.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter
from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.pauses import PERSON_KINDS, Pause
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.value_objects import MessageDraft
from app.modules.agent.domain.wait import AgentWaitWakeReason
from app.modules.agent.infrastructure import repositories
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.services.conversation_resume_return import (
    ResumeToolReturnBuilder,
)
from app.modules.agent.services.wait_timers import disarm_wait


class SupersedeStalePauses:
    """DENY every pause nobody decided, and cancel a wait typed past."""

    def __init__(
        self, uow: SqlAlchemyUnitOfWork, *, resume_returns: ResumeToolReturnBuilder
    ) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.messages = Messages(uow)
        self.pauses = Pauses(uow)
        self.resume_returns = resume_returns

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> SupersedeStalePauses:
        return cls(
            uow,
            resume_returns=ResumeToolReturnBuilder(
                uow, repositories.AgentRepository(uow)
            ),
        )

    async def supersede(
        self,
        *,
        conversation: Conversation,
        user_id: UUID,
    ) -> list[Message]:
        """Close what the person moved past; return the messages it wrote.

        A ``wait_for`` the person typed past is cancelled first, with its timer,
        in the same (caller's) transaction: left PENDING, its timer would later
        fire into a conversation that had moved on and start a resume nobody
        asked for.

        Then every pause a person had to decide is superseded. This only ever
        runs when no run is currently active (the caller checked that already),
        so a pause still open belongs to a run that already finished -- the
        person moved on without answering it. Always DENY, never approve: this
        is a safety fallback synthesizing a response on their behalf, not a
        decision, so a request_approval must never auto-execute here. That also
        rules out side effects Lemma could not take back on a rollback, which
        is why an Agent Host permission is not delivered from here.
        """
        cancelled = await self._cancel_superseded_wait(conversation)
        superseded: list[Message] = []
        for pause in await self.pauses.pending(conversation.id, kinds=PERSON_KINDS):
            saved_return = await self._deny(conversation, pause, user_id=user_id)
            if saved_return is not None:
                superseded.append(saved_return)
        return [*cancelled, *superseded]

    async def _cancel_superseded_wait(
        self, conversation: Conversation
    ) -> list[Message]:
        # Lazy: the waiting tool's models import the tool registry, which
        # reaches back into the pause use cases.
        from app.modules.agent.infrastructure.tools.waiting.models import (
            build_wait_result,
            elapsed_seconds,
        )

        pending = await self.pauses.pending(conversation.id, kinds=(PauseKind.WAIT,))
        if not pending:
            return []
        # Conversation, then wait: the order every wait ending takes.
        await self.conversations.lock_conversation(conversation.id)
        wait = await self.pauses.by_id(pending[0].id, lock=True)
        if wait is None or not wait.cancel() or not await self.pauses.save(wait):
            return []
        await disarm_wait(WorkWriter.of(self.uow), wait)
        if wait.returned or wait.wait_type is None or wait.wait_spec is None:
            return []
        saved = await self.messages.append_message(
            conversation_id=conversation.id,
            agent_run_id=wait.run_id,
            draft=MessageDraft.of_tool_return(
                tool_call_id=wait.tool_call_id,
                tool_name=wait.tool_name,
                tool_result=build_wait_result(
                    wait_type=wait.wait_type,
                    reason=AgentWaitWakeReason.CANCELLED,
                    waited_seconds=elapsed_seconds(wait.wait_spec.started_at),
                    note_to_self=wait.wait_spec.note_to_self,
                ),
            ),
        )
        return [saved]

    async def _deny(
        self, conversation: Conversation, pause: Pause, *, user_id: UUID
    ) -> Message | None:
        if not pause.supersede(by=user_id) or not await self.pauses.save(pause):
            # A concurrent resolve already decided it; that resolve (or its
            # own reconcile) owns synthesizing the return.
            return None
        tool_name, tool_result = await self.resume_returns.build(
            conversation=conversation,
            user_id=user_id,
            pause=pause,
            deliver_to_host=False,
        )
        return await self.messages.append_message(
            conversation_id=conversation.id,
            agent_run_id=pause.run_id,
            draft=MessageDraft.of_tool_return(
                tool_call_id=pause.tool_call_id,
                tool_name=tool_name,
                tool_result=tool_result,
            ),
        )
