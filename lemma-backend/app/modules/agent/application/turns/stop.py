"""Stopping a turn.

Stop is the start of a turn in reverse, and the asymmetry is deliberate: a stop
closes any pause the run was sitting on, but never starts the resume run that
closing a pause normally triggers. Stop means stop.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.application.pauses.supersede import SupersedeStalePauses
from app.modules.agent.domain.delegation import MAX_CHILDREN_PER_RUN
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pauses import PERSON_KINDS
from app.modules.agent.domain.ports import AgentRepository
from app.modules.agent.domain.value_objects import ConversationStatus
from app.modules.agent.infrastructure import repositories
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.pauses import Awaiting, Pauses
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.conversation_access import (
    require_agent_action,
    resolve_expected_agent_id,
    validate_conversation_access,
)
from app.modules.agent.infrastructure.live import publish_conversation_event
from app.modules.agent.domain.live_events import message_payload
from app.modules.agent.services.run_stop import stop_run
from app.modules.agent.domain.live_events import message_to_payload


#: Children read per stop: every one a run may have, several runs over.
_CHILDREN_STOPPED_AT_ONCE = 4 * MAX_CHILDREN_PER_RUN


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
        self.pauses = Pauses(uow)
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
        # Stopping a delegation stops what it delegated: a person who stops a
        # runaway fan-out does not go on paying for every child.
        await self._stop_children(conversation)
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

        # No active run, but the conversation may still be suspended: the run
        # that paused ended cleanly when the tool paused it, so without this
        # Stop silently did nothing -- the wait still fired later, and the
        # approval card stayed up.
        await self._close_open_pauses(conversation=conversation, user_id=user_id)
        await self._keep_a_decided_pause_from_resuming(conversation=conversation)
        await self.uow.commit()
        return conversation

    async def _stop_children(self, conversation: Conversation) -> None:
        """Stop each sub-agent still running or waiting, in this transaction.

        One level: a sub-agent delegates no further (``MAX_DEPTH``). Owned by
        the same person through the parent, so no grant is asked again.
        """
        children = await self.conversations.unfinished_children(
            conversation.id, limit=_CHILDREN_STOPPED_AT_ONCE
        )
        for child in children:
            run = await self.runs.get_active_agent_run_for_update(child.id)
            if run is not None:
                await stop_run(
                    self.uow,
                    self.runs,
                    pod_id=child.pod_id,
                    conversation_id=child.id,
                    run_id=run.id,
                    stop_requested_after_sequence=None,
                )
                continue
            await self.supersede.supersede(conversation=child, user_id=child.user_id)
            await self.conversations.set_conversation_status(
                conversation_id=child.id, status=ConversationStatus.STOPPED
            )

    async def _close_open_pauses(
        self, *, conversation: Conversation, user_id: UUID
    ) -> None:
        """Cancel a wait, and DENY what the person decided not to answer.

        The wait's CANCELLED return is still written, so the paused call is not
        left dangling in history; whatever it waited on is left alone -- a
        process dies at its own ceiling, a child run is stopped by stopping the
        child. DENY, never approve, for the reason ``SupersedeStalePauses``
        gives. And no resume run follows either: Stop means the agent does not
        wake.
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
        in_flight = await self.pauses.pending(
            conversation.id, kinds=PERSON_KINDS, awaiting=Awaiting.RETURN
        )
        if not in_flight or conversation.status == ConversationStatus.STOPPED:
            return
        await self.conversations.set_conversation_status(
            conversation_id=conversation.id,
            status=ConversationStatus.STOPPED,
        )
        conversation.status = ConversationStatus.STOPPED
        await self.uow.commit()
