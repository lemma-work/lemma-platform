"""Stopping a turn.

Stop is the start of a turn in reverse, and the asymmetry is deliberate: a stop
closes any pause the run was sitting on, but never starts the resume run that
closing a pause normally triggers. Stop means stop.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter
from app.modules.agent.application.pauses.supersede import SupersedeStalePauses
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pausing_tools import (
    USER_PAUSING_TOOL_NAMES,
    WAIT_TOOL_NAME,
)
from app.modules.agent.domain.ports import AgentRepository
from app.modules.agent.domain.value_objects import ConversationStatus
from app.modules.agent.domain.wait import AgentWaitWakeReason
from app.modules.agent.infrastructure import repositories
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.conversation_access import (
    require_agent_action,
    resolve_expected_agent_id,
    validate_conversation_access,
)
from app.modules.agent.services.pause_resume import append_pause_tool_return
from app.modules.agent.infrastructure.live import publish_conversation_event
from app.modules.agent.domain.live_events import message_payload
from app.modules.agent.services.run_stop import stop_run
from app.modules.agent.domain.live_events import message_to_payload
from app.modules.agent.services.wait_timers import disarm_wait
from app.modules.agent.tools.waiting.models import (
    build_wait_result,
    elapsed_seconds,
)


class StopConversation:
    """Stops the run in flight, or the pause a conversation is sitting on."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        agent_repository: AgentRepository,
        supersede: SupersedeStalePauses,
    ) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.runs = Runs(uow)
        self.messages = Messages(uow)
        self.pausing_calls = Pauses(uow)
        self.agent_repository = agent_repository
        self.supersede = supersede

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> StopConversation:
        return cls(
            uow,
            agent_repository=repositories.AgentRepository(uow),
            supersede=SupersedeStalePauses.build(uow),
        )

    async def stop(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
    ) -> Conversation:
        """Stop the run in flight, closing whatever pause it was sitting on."""
        expected_agent_id = await resolve_expected_agent_id(
            self.agent_repository,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        conversation = validate_conversation_access(
            await self.conversations.get_conversation(conversation_id),
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        await require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=conversation.agent_id,
            action=Permissions.AGENT_EXECUTE,
        )
        active_run = await self.runs.get_active_agent_run_for_update(conversation.id)
        if active_run is not None:
            # What is said from here on is owed an answer once the run has
            # stopped; what was said before it is not (`StartQueuedFollowUp`).
            said_before_stop = await self.messages.latest_message_sequence(
                conversation.id
            )
            # A turn still queued is dropped and the run ends STOPPED here; one a
            # worker holds is asked to stop and ends at its next check.
            finish_result = await stop_run(
                self.uow,
                self.runs,
                pod_id=conversation.pod_id,
                conversation_id=conversation.id,
                run_id=active_run.id,
                stop_requested_after_sequence=said_before_stop,
            )
            if finish_result is not None:
                conversation.status = finish_result.conversation_status
            await self.uow.commit()
            return conversation

        # No active run, but the conversation may still be suspended. Neither
        # pause has a run by construction — both ended cleanly when the tool
        # paused them — so without these, Stop silently did nothing: the wait
        # still fired later, and the approval card stayed up.
        await self._cancel_active_wait(conversation=conversation)
        await self._deny_unresolved_pauses(conversation=conversation, user_id=user_id)
        await self._keep_a_decided_pause_from_resuming(conversation=conversation)
        return conversation

    @property
    def wait_repository(self) -> AgentConversationWaitRepository:
        # Built on demand rather than in __init__: the repository binds a session
        # eagerly, and plenty of callers construct this use case without a real
        # unit of work to exercise paths that never touch the database.
        return AgentConversationWaitRepository(self.uow)

    async def _cancel_active_wait(self, *, conversation: Conversation) -> None:
        """Stop a waiting agent for good: drop the wait, never resume.

        Whatever it was waiting on is left alone. Stop means stop the agent, and
        killing someone's build because they stopped a chat is a surprise they
        did not ask for -- a process dies at its own ceiling anyway, and a child
        run is stopped by stopping the child.

        The CANCELLED tool return is still written, so the paused call is not
        left dangling in history — a tool call with no return is dropped when
        history is rebuilt, and the model would see a turn that ends mid-thought.
        What is deliberately skipped is the resume run (``ResumeRun``): Stop
        means the agent does not wake.
        """
        active = await self.wait_repository.find_active_for_conversation(
            conversation.id
        )
        if active is None:
            return
        # Under the conversation's lock and then the wait's, like every other
        # ending: a timer firing now either took them first (and woke it --
        # this finds it ended) or waits for this commit and finds it
        # CANCELLED. Exactly one of the two happens.
        await self.conversations.lock_conversation(conversation.id)
        wait = await self.wait_repository.claim(active.id)
        if wait is None:
            return

        wait.cancel()
        await self.wait_repository.update(wait)
        # Its queued timer goes with it, in this transaction. A poll already
        # running re-reads the wait under the lock and does not re-arm.
        await disarm_wait(WorkWriter.of(self.uow), wait)
        await self.conversations.set_conversation_status(
            conversation_id=conversation.id,
            status=ConversationStatus.STOPPED,
        )
        conversation.status = ConversationStatus.STOPPED
        await append_pause_tool_return(
            self.uow,
            pausing_calls=self.pausing_calls,
            messages=self.messages,
            conversation=conversation,
            paused_run_id=wait.agent_run_id,
            tool_call_id=wait.tool_call_id,
            tool_name=WAIT_TOOL_NAME,
            tool_result=build_wait_result(
                wait_type=wait.wait_type,
                reason=AgentWaitWakeReason.CANCELLED,
                waited_seconds=elapsed_seconds((wait.spec or {}).get("started_at")),
                note_to_self=(wait.spec or {}).get("note_to_self"),
            ),
        )

    async def _deny_unresolved_pauses(
        self, *, conversation: Conversation, user_id: UUID
    ) -> None:
        """Close an ask_user/request_approval the person decided not to answer.

        A conversation paused this way has no active run either — the run that
        asked finished when the tool paused it — so the branch above found
        nothing and Stop returned the conversation untouched, on the one state
        where someone most wants to abandon a turn.

        DENY, never approve, for the reason ``SupersedeStalePauses`` gives:
        this is a response synthesized on the user's behalf, not a decision
        they made. And, as with a wait, no resume run follows.
        """
        superseded = await self.supersede.supersede(
            conversation=conversation,
            user_id=user_id,
        )
        if not superseded:
            return

        await self.conversations.set_conversation_status(
            conversation_id=conversation.id,
            status=ConversationStatus.STOPPED,
        )
        conversation.status = ConversationStatus.STOPPED

        # Published after the commit, not inside it: these are live UI frames
        # and sending them from here would hold a pooled connection across a
        # Redis round trip. Same arrangement as `AdmitMessage`.
        frames = [
            message_payload(item.agent_run_id, message_to_payload(item))
            for item in superseded
        ]

        async def _publish_frames() -> None:
            for frame in frames:
                await publish_conversation_event(conversation.id, frame)

        self.uow.after_commit(_publish_frames)
        await self.uow.commit()

    async def _keep_a_decided_pause_from_resuming(
        self, *, conversation: Conversation
    ) -> None:
        """Stop an approval already decided from waking the agent afterwards.

        Superseding only closes pauses nobody decided. One the person approved
        is running its tool in a worker, which cannot be taken back -- but when
        it finishes it would start the resume run, and the agent would carry on
        after Stop. STOPPED is what ``ResumeRun`` refuses on.
        """
        in_flight = await self.pausing_calls.pausing_calls_decided_awaiting_a_return(
            conversation_id=conversation.id,
            pausing_tool_names=USER_PAUSING_TOOL_NAMES,
        )
        if not in_flight or conversation.status == ConversationStatus.STOPPED:
            return
        await self.conversations.set_conversation_status(
            conversation_id=conversation.id,
            status=ConversationStatus.STOPPED,
        )
        conversation.status = ConversationStatus.STOPPED
        await self.uow.commit()
