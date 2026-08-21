"""The pause/resume primitive shared by every tool that suspends a turn.

``ask_user`` / ``request_approval`` were the first pausing tools, so this
machinery grew inside the approval path in ``conversation_service``. It is not
approval-specific: any tool that raises ``AgentInputRequired`` pauses the same
way, and resumes by having its return synthesized and replayed by a fresh run.
``snooze`` is the second caller — it resolves on a timer instead of on a person.

A collaborator rather than a mixin: it needs a unit of work, a conversation
repository and an agent repository, and nothing else ``ConversationService``
holds. Stated as constructor arguments, that is checkable; mixed in, it was a
set of attribute names the mixin hoped its host had -- which is how it came to
call a private helper the host later stopped having.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.log.log import get_logger
from app.modules.agent.services.conversation_access import (
    resolve_agent,
)
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.events import AgentRunStartedEvent
from app.modules.agent.domain.pausing_tools import (
    PAUSING_TOOL_NAMES as _PAUSING_TOOL_NAMES_DOMAIN,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    MessageDraft,
    MessageKind,
)
from app.modules.agent.services.realtime import (
    message_payload,
    publish_conversation_event,
)
from app.modules.agent.services.pod_runtime_defaults import (
    default_agent_runtime_for_pod,
)
from app.modules.agent.services.serialization import message_to_payload

logger = get_logger(__name__)

# Defined in the domain because history reconstruction needs the same list —
# see `domain/pausing_tools`. Re-exported here so existing callers are unchanged.
PAUSING_TOOL_NAMES = _PAUSING_TOOL_NAMES_DOMAIN


class PauseResume:
    """Suspends and resumes a turn around a tool that waits for the world."""

    def __init__(
        self,
        uow: object,
        conversation_repository: object,
        agent_repository: object,
    ) -> None:
        self.uow = uow
        self.conversation_repository = conversation_repository
        self.agent_repository = agent_repository

    async def append_pause_tool_return(
        self,
        *,
        conversation: Conversation,
        paused_run_id: UUID,
        tool_call_id: str,
        tool_name: str,
        tool_result: object,
    ) -> bool:
        """Persist the return the resumed run will replay. Idempotent.

        Returns True if it wrote one, False if a return already existed — the
        caller uses that to avoid re-running side effects (an approved tool must
        execute at most once). Persisted under the *paused* run, since history is
        reconstructed per conversation and ``_build_tool_batch`` pairs a return
        with its call regardless of which run each lives in.
        """
        existing = await self.conversation_repository.get_tool_return(
            conversation_id=conversation.id,
            tool_call_id=tool_call_id,
        )
        if existing is not None:
            return False
        saved_return = await self.conversation_repository.append_message(
            conversation_id=conversation.id,
            agent_run_id=paused_run_id,
            draft=MessageDraft.of_tool_return(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_result=tool_result,
            ),
        )
        await self.uow.commit()
        # The commit above already returned the connection, so this publish
        # holds nothing -- but only a reader could tell, and the gate is
        # lexical. Saying it in the structure makes it checkable, and makes a
        # future edit that moves the publish above the commit fail loudly.
        async with connection_released(getattr(self.uow, "session", None)):
            await publish_conversation_event(
                conversation.id,
                message_payload(paused_run_id, message_to_payload(saved_return)),
            )
        return True

    async def resume_would_duplicate_a_live_turn(self, paused_run_id: UUID) -> bool:
        """Is the "paused" run actually still running?

        A remote harness's `ask_user` / `request_approval` parks rather than
        pausing: the run keeps running while the host's MCP bridge holds the
        tool response open, and the synthesized return appended by the
        resolution is exactly what that bridge is polling for. The turn carries
        on the moment it reads it, so resuming would put two runs on one turn.

        Keyed on the run still running rather than on a marker in the call's
        arguments, unlike the ACP permission path: those arguments come from the
        model, not from Lemma, so there is nothing of ours to read back. It is
        also the honest condition -- a run still running is precisely what makes
        a resume wrong.
        """
        run = await self.conversation_repository.get_agent_run(paused_run_id)
        return run is not None and run.status == AgentRunStatus.RUNNING

    async def start_resume_run_if_ready(
        self,
        *,
        conversation: Conversation,
        paused_run_id: UUID,
        resumed_tool_call_id: str,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
        source: str,
    ) -> None:
        """Start the run that replays the synthesized return, at most once.

        A turn can pause with several pending interactions (e.g. request_approval
        + ask_user in one assistant turn, or an ask_user alongside a snooze).
        Resume only once every pausing call in the paused run is resolved —
        otherwise the unresolved sibling is orphaned (no return), dropped from the
        resumed run's history, and the agent re-asks it. The conversation lock
        serializes this so two near-simultaneous resolutions don't each start one.
        """
        await self.conversation_repository.lock_conversation(conversation.id)
        remaining = await self._unresolved_pausing_call_ids(
            conversation_id=conversation.id,
            agent_run_id=paused_run_id,
        )
        if remaining:
            # Still paused on something else; that resolution will start the run.
            await self.uow.commit()
            return
        active_run = await self.conversation_repository.get_active_agent_run_for_update(
            conversation.id
        )
        if active_run is not None:
            # Another resolution already started the resume run (or a normal run is
            # live); it will replay the now-complete tool returns. Nothing to do.
            await self.uow.commit()
            return
        agent = await resolve_agent(
            conversation,
            user_id=user_id,
            agent_repository=self.agent_repository,
        )
        selected_agent_runtime = (
            conversation.agent_runtime
            or agent.agent_runtime
            or await default_agent_runtime_for_pod(self.uow, pod_id=conversation.pod_id)
        )
        resume_run = await self.conversation_repository.create_agent_run(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            agent_runtime=selected_agent_runtime,
            metadata={"source": source, "resumed_tool_call_id": resumed_tool_call_id},
        )
        self.uow.collect_events(
            [
                AgentRunStartedEvent(
                    conversation_id=conversation.id,
                    agent_run_id=resume_run.id,
                    user_id=user_id,
                    pod_id=pod_id,
                    agent_name=agent_name,
                )
            ]
        )
        await self.uow.commit()

    async def _unresolved_pausing_call_ids(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID,
    ) -> list[str]:
        """Pausing tool calls in the paused run that are still outstanding.

        A call counts as resolved once it has *either* a recorded approval
        decision or a persisted tool return. Approvals record the decision first
        and build the return second, so the decision is what unblocks them; a
        snooze has no decision at all and is resolved purely by its return. Taking
        the union means one check serves both without either knowing about the
        other.
        """
        resolved_ids = await self.conversation_repository.list_resolved_approval_ids(
            conversation_id=conversation_id
        )
        messages, _ = await self.conversation_repository.list_messages(
            conversation_id=conversation_id,
            limit=500,
        )
        returned_ids = {
            message.tool_call_id
            for message in messages
            if message.kind == MessageKind.TOOL_RETURN
            and message.tool_call_id is not None
        }
        resolved = set(resolved_ids) | returned_ids
        return [
            message.tool_call_id
            for message in messages
            if message.kind == MessageKind.TOOL_CALL
            and message.tool_name in PAUSING_TOOL_NAMES
            and message.agent_run_id == agent_run_id
            and message.tool_call_id is not None
            and message.tool_call_id not in resolved
        ]
