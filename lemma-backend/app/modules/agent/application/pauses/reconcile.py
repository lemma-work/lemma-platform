"""Finishing a recorded decision that runs nothing: write its return, resume.

An answer, a denial, an Agent Host permission (a queued command), or a decision
whose return already exists -- everything but an approved tool, which is the
worker's (``events/approval_work``). Safe to call again and again: a resolve
that died between recording the decision and starting the run self-heals the
next time anybody resolves it, because each half checks for its own effect.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.application.pauses.resume import ResumeRun
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pauses import Pause
from app.modules.agent.domain.value_objects import RunTrigger
from app.modules.agent.infrastructure import repositories
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.services.conversation_resume_return import (
    ResumeToolReturnBuilder,
)


class ReconcileDecision:
    """Synthesizes a decided pause's return exactly once, then resumes its run."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        resume_returns: ResumeToolReturnBuilder,
        resume: ResumeRun,
    ) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.pauses = Pauses(uow)
        self.resume_returns = resume_returns
        self.resume = resume

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> ReconcileDecision:
        return cls(
            uow,
            resume_returns=ResumeToolReturnBuilder(
                uow, repositories.AgentRepository(uow)
            ),
            resume=ResumeRun.build(uow),
        )

    async def reconcile(
        self,
        *,
        conversation: Conversation,
        pause: Pause,
        user_id: UUID,
        agent_name: str | None,
    ) -> None:
        """Finish (or re-finish) a decision that runs nothing; safe to repeat.

        Two idempotent halves:
          1. Synthesize + persist the paused call's tool return exactly once.
          2. Start the resume run only once every pausing call in the paused
             run is resolved and no run is already active (`ResumeRun`).

        A resolve that died mid-flight (decision committed, return not appended,
        or run not started) self-heals when this runs again on the user's retry.
        """
        # Two resolvers adopting one decision (a tap and a click) both get here;
        # the conversation lock makes the second see the first one's return.
        await self.conversations.lock_conversation(conversation.id)
        existing_return = await self.pauses.tool_return(
            conversation_id=conversation.id, tool_call_id=pause.tool_call_id
        )
        tool_return = None
        if existing_return is None:
            tool_return = await self.resume_returns.build(
                conversation=conversation, user_id=user_id, pause=pause
            )
        await self.resume.resume(
            conversation=conversation,
            paused_run_id=pause.run_id,
            tool_call_id=pause.tool_call_id,
            user_id=user_id,
            agent_name=agent_name,
            trigger=RunTrigger.RESUME,
            tool_return=tool_return,
            decided=pause,
        )
