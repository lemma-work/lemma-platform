"""Resuming the run a pause ended, once what it waited for has answered.

``ask_user`` / ``request_approval`` were the first pausing tools, so this grew
inside the approval path. It is not approval-specific: any tool that raises
``AgentInputRequired`` pauses the same way, and resumes by having its return
written and replayed by a fresh run. ``wait_for`` resumes here too -- on a
timer, a process or a child run instead of a person.

Idempotent on both halves: the return is written at most once (an approved
tool must not run twice because its return was written twice), and the resume
run starts at most once, under the conversation lock, and only when every
pausing call of the paused run has its answer.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pauses import Pause
from app.modules.agent.domain.ports import AgentRepository
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    RunTrigger,
)
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.infrastructure.db.queued_messages import QueuedMessages
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure import repositories
from app.modules.agent.services.pause_resume import (
    append_pause_tool_return,
    start_resume_run,
)
from app.modules.usage.contracts.execution import UsageService, build_usage_service


class ResumeRun:
    """Writes a pause's return and starts the run that replays it."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        agent_repository: AgentRepository,
        usage_service: UsageService | None = None,
    ) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.runs = Runs(uow)
        self.messages = Messages(uow)
        self.queued_messages = QueuedMessages(uow)
        self.pauses = Pauses(uow)
        self.agent_repository = agent_repository
        self.usage_service = usage_service

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> ResumeRun:
        return cls(
            uow,
            agent_repository=repositories.AgentRepository(uow),
            usage_service=build_usage_service(uow),
        )

    async def resume(
        self,
        *,
        conversation: Conversation,
        paused_run_id: UUID,
        tool_call_id: str,
        user_id: UUID,
        trigger: RunTrigger,
        agent_name: str | None = None,
        tool_return: tuple[str, object] | None = None,
        decided: Pause | None = None,
    ) -> None:
        """Write ``tool_return`` (when given), then start the resume run if ready.

        ``tool_return`` is ``(tool_name, tool_result)``, written under the
        paused run unless a return already exists; it commits on its own.

        ``decided`` is the pause a person decided. Its run may still be going,
        and then it is not resumed: an Agent Host permission is carried on by
        the host itself, and a parked call is polled by the live run. A wait's
        wake passes none.
        """
        if tool_return is not None:
            tool_name, tool_result = tool_return
            await append_pause_tool_return(
                self.uow,
                pauses=self.pauses,
                messages=self.messages,
                conversation=conversation,
                paused_run_id=paused_run_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_result=tool_result,
            )
        if decided is not None and await self._answered_inside_a_live_run(decided):
            return
        await self._start_if_ready(
            conversation=conversation,
            paused_run_id=paused_run_id,
            resumed_tool_call_id=tool_call_id,
            user_id=user_id,
            agent_name=agent_name,
            trigger=trigger,
        )

    async def _answered_inside_a_live_run(self, decided: Pause) -> bool:
        host_permission = decided.kind is PauseKind.HOST_PERMISSION
        if host_permission and not await self._turn_ended_while_parked(decided.run_id):
            # An Agent Host pauses *inside* a live run: the decision was just
            # handed to the host, which carries the same run on from where it
            # stopped. Starting a resume run here would dispatch a second,
            # duplicate host run for the same turn. Unless Lemma ended that
            # turn because the request sat past its parked window: then the
            # decision reaches the agent only by resuming, like any pause.
            return True
        return await self._resume_would_duplicate_a_live_turn(decided)

    async def _turn_ended_while_parked(self, paused_run_id: UUID) -> bool:
        run = await self.runs.get_agent_run(paused_run_id)
        return run is not None and run.state.suspended_while_parked is not None

    async def _resume_would_duplicate_a_live_turn(self, decided: Pause) -> bool:
        """Is this call parked inside a run that is still going?

        A remote harness's `ask_user` / `request_approval` parks rather than
        pausing: the run keeps running while the host's MCP bridge holds the
        tool response open, and the synthesized return appended by the
        resolution is exactly what that bridge is polling for. The turn carries
        on the moment it reads it, so resuming would put two runs on one turn.

        Keyed on the mark Lemma put on the call when it parked it, and not on
        the run's status alone. An in-process pause answered before its run had
        finished being written as WAITING also finds the run still RUNNING --
        and nothing was polling for that answer, so skipping the resume left
        the conversation waiting on a question already answered.
        """
        if not decided.parked:
            return False
        run = await self.runs.get_agent_run(decided.run_id)
        return run is not None and run.status == AgentRunStatus.RUNNING

    async def _start_if_ready(
        self,
        *,
        conversation: Conversation,
        paused_run_id: UUID,
        resumed_tool_call_id: str,
        user_id: UUID,
        agent_name: str | None,
        trigger: RunTrigger,
    ) -> None:
        """Start the run that replays the synthesized return, at most once.

        A turn can pause with several pending interactions (e.g. request_approval
        + ask_user in one assistant turn, or an ask_user alongside a wait_for).
        Resume only once every pausing call in the paused run is resolved —
        otherwise the unresolved sibling is orphaned (no return), dropped from the
        resumed run's history, and the agent re-asks it. The conversation lock
        serializes this so two near-simultaneous resolutions don't each start one.

        Not after Stop. An approval decided before Stop still finishes running
        its tool -- it cannot be taken back -- but the agent does not wake to
        carry on: Stop leaves the conversation STOPPED, and that is read here.
        """
        await self.conversations.lock_conversation(conversation.id)
        remaining = await self.pauses.pending(conversation.id, run_id=paused_run_id)
        if remaining:
            # Still paused on something else; that resolution will start the run.
            await self.uow.commit()
            return
        active_run = await self.runs.get_active_agent_run_for_update(conversation.id)
        if active_run is not None:
            # Another resolution already started the resume run, or the paused
            # run has not finished being written yet -- in which case its own
            # finalization resumes it (`StartQueuedFollowUp`).
            await self.uow.commit()
            return
        status = await self.conversations.get_conversation_status(conversation.id)
        if status == ConversationStatus.STOPPED:
            await self.uow.commit()
            return
        await start_resume_run(
            self.uow,
            runs=self.runs,
            queued_messages=self.queued_messages,
            agent_repository=self.agent_repository,
            usage_service=self.usage_service,
            conversation=conversation,
            paused_run_id=paused_run_id,
            resumed_tool_call_id=resumed_tool_call_id,
            user_id=user_id,
            agent_name=agent_name,
            trigger=trigger,
            publish_claimed=True,
        )
