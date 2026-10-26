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
from app.core.log.log import get_logger
from app.modules.agent.domain.approvals import SUPERSEDED_KEY
from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.pausing_tools import PAUSING_TOOL_NAMES
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
    MessageDraft,
)
from app.modules.agent.infrastructure.db.approvals import ApprovalDecisions
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.infrastructure import repositories
from app.modules.agent.services.conversation_resume_return import (
    ResumeToolReturnBuilder,
)

logger = get_logger(__name__)


class SupersedeStalePauses:
    """DENY every pause nobody decided, and cancel a wait typed past."""

    def __init__(
        self, uow: SqlAlchemyUnitOfWork, *, resume_returns: ResumeToolReturnBuilder
    ) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.messages = Messages(uow)
        self.pausing_calls = Pauses(uow)
        self.approval_decisions = ApprovalDecisions(uow)
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
        in the same (caller's) transaction: left ACTIVE, its timer would later
        fire into a conversation that had moved on and start a resume nobody
        asked for.

        Then every ask_user/request_approval call left unresolved from an
        earlier WAITING pause is denied. This only ever runs when no run is
        currently active (the caller checked that already), so any pausing call
        still unresolved at this point belongs to a run that already finished —
        the user moved on without answering it. Always DENY, never approve:
        this is a safety fallback synthesizing a response on the user's behalf,
        not a real decision, so a request_approval must never auto-execute
        here. That also rules out side effects Lemma could not take back on a
        rollback, which is why an Agent Host permission is not delivered from
        here.
        """
        cancelled = await self._cancel_superseded_wait(conversation)
        stale = await self.pausing_calls.pausing_calls_awaiting_a_decision(
            conversation_id=conversation.id,
            pausing_tool_names=PAUSING_TOOL_NAMES,
        )
        superseded: list[Message] = []
        for message in stale:
            saved_return = await self._deny(
                conversation=conversation, message=message, user_id=user_id
            )
            if saved_return is not None:
                superseded.append(saved_return)
        return [*cancelled, *superseded]

    async def _cancel_superseded_wait(
        self, conversation: Conversation
    ) -> list[Message]:
        # Lazy: the waiting tool's models import the tool registry, which
        # reaches back into the pause use cases.
        from app.core.infrastructure.work import WorkWriter
        from app.modules.agent.domain.pausing_tools import WAIT_TOOL_NAME
        from app.modules.agent.domain.wait import AgentWaitWakeReason
        from app.modules.agent.infrastructure.wait_repository import (
            AgentConversationWaitRepository,
        )
        from app.modules.agent.services.wait_timers import disarm_wait
        from app.modules.agent.infrastructure.tools.waiting.models import (
            build_wait_result,
            elapsed_seconds,
        )

        waits = AgentConversationWaitRepository(self.uow)
        active = await waits.find_active_for_conversation(conversation.id)
        if active is None:
            return []
        # Conversation, then wait: the order every wait ending takes.
        await self.conversations.lock_conversation(conversation.id)
        wait = await waits.claim(active.id)
        if wait is None or not wait.cancel():
            return []
        await waits.update(wait)
        await disarm_wait(WorkWriter.of(self.uow), wait)
        existing = await self.pausing_calls.get_tool_return(
            conversation_id=conversation.id, tool_call_id=wait.tool_call_id
        )
        if existing is not None:
            return []
        spec = wait.wait_spec
        saved = await self.messages.append_message(
            conversation_id=conversation.id,
            agent_run_id=wait.agent_run_id,
            draft=MessageDraft.of_tool_return(
                tool_call_id=wait.tool_call_id,
                tool_name=WAIT_TOOL_NAME,
                tool_result=build_wait_result(
                    wait_type=wait.wait_type,
                    reason=AgentWaitWakeReason.CANCELLED,
                    waited_seconds=elapsed_seconds(spec.started_at),
                    note_to_self=spec.note_to_self,
                ),
            ),
        )
        return [saved]

    async def _deny(
        self,
        *,
        conversation: Conversation,
        message: Message,
        user_id: UUID,
    ) -> Message | None:
        # Re-established rather than assumed: the caller's filter proves the
        # call id and the tool name, but none of that survives being passed as a
        # `Message`. A call missing any of the three cannot have a faithful
        # return synthesized for it.
        tool_call_id = message.tool_call_id
        tool_name = message.tool_name
        agent_run_id = message.agent_run_id
        if tool_call_id is None or tool_name is None or agent_run_id is None:
            # Only the run id is genuinely unproven here, and losing it is not
            # cosmetic: the pause stays unresolved, so the next run rebuilds a
            # history still showing an open question nobody can now answer.
            # Said out loud because the alternative is a conversation that
            # quietly stops making sense.
            logger.warning(
                "agent.conversation_approvals.pause_without_run_skipped.degraded",
                conversation_id=str(conversation.id),
                tool_call_id=tool_call_id,
                tool_name=tool_name,
            )
            return None
        tool_args = message.tool_args if isinstance(message.tool_args, dict) else {}
        decision_tool_name = (
            "ask_user"
            if tool_name == "ask_user"
            else str(tool_args.get("tool_name") or "request_approval")
        )
        response: JsonObject = {SUPERSEDED_KEY: True}
        recorded = await self.approval_decisions.record_approval_decision(
            conversation_id=conversation.id,
            approval_id=tool_call_id,
            agent_run_id=agent_run_id,
            tool_name=decision_tool_name,
            decision=AgentRunApprovalDecision.DENY,
            response=response,
            resolved_by_user_id=user_id,
        )
        if not recorded:
            # A concurrent resolve already recorded a real decision for this
            # call; that resolve (or its own reconcile) owns synthesizing the
            # return.
            return None
        existing_return = await self.pausing_calls.get_tool_return(
            conversation_id=conversation.id,
            tool_call_id=tool_call_id,
        )
        if existing_return is not None:
            return None
        return_tool_name, tool_result = await self.resume_returns.build(
            conversation=conversation,
            user_id=user_id,
            kind=tool_name,
            tool_args=tool_args,
            decision=AgentRunApprovalDecision.DENY,
            response=response,
            paused_agent_run_id=agent_run_id,
            deliver_to_host=False,
        )
        return await self.messages.append_message(
            conversation_id=conversation.id,
            agent_run_id=agent_run_id,
            draft=MessageDraft.of_tool_return(
                tool_call_id=tool_call_id,
                tool_name=return_tool_name,
                tool_result=tool_result,
            ),
        )
