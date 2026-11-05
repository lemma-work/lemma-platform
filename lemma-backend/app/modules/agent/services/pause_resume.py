"""The pause/resume primitive shared by every tool that suspends a turn.

``ask_user`` / ``request_approval`` were the first pausing tools, so this
machinery grew inside the approval path in ``conversation_service``. It is not
approval-specific: any tool that raises ``AgentInputRequired`` pauses the same
way, and resumes by having its return synthesized and replayed by a fresh run.
``wait_for`` is the second caller — it resolves on a timer, a process or a child
run instead of on a person.

A collaborator rather than a mixin: it needs a unit of work, a conversation
repository and an agent repository, and nothing else ``ConversationService``
holds. Stated as constructor arguments, that is checkable; mixed in, it was a
set of attribute names the mixin hoped its host had -- which is how it came to
call a private helper the host later stopped having.
"""

from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent.services.runtime_availability import turn_runtime
from app.modules.agent.services.conversation_access import (
    resolve_agent,
)
from app.modules.agent.domain.private_notes import privacy_of, run_is_private
from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.contracts.turns import stage_agent_turn, turn_for
from app.modules.agent.domain.pausing_tools import (
    PARKED_IN_RUN_KEY,
    PAUSING_TOOL_NAMES as _PAUSING_TOOL_NAMES_DOMAIN,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.ports import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    MessageDraft,
)
from app.modules.agent.services.queued_followups import LEFTOVERS_KEY
from app.modules.agent.services.realtime import (
    error_payload,
    message_payload,
    publish_conversation_event,
)
from app.modules.agent.services.serialization import message_to_payload
from app.modules.agent.services.runtime_owned_uow import RuntimeOwnedUnitOfWork
from app.modules.agent.services.usage_preflight import assert_usage_preflight_allowed
from app.modules.usage.contracts import UsageLimitExceededError
from app.modules.usage.contracts.execution import UsageService

logger = get_logger(__name__)

# Defined in the domain because history reconstruction needs the same list —
# see `domain/pausing_tools`. Re-exported here so existing callers are unchanged.
PAUSING_TOOL_NAMES = _PAUSING_TOOL_NAMES_DOMAIN

#: What a run that resumes a pause hands back to whoever publishes for it: the
#: run's id, and the queued messages it claimed, whose frames are owed.
ResumeStarted = tuple[UUID, list[Message]]


class PauseResume:
    """Suspends and resumes a turn around a tool that waits for the world."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        conversation_repository: ConversationRepository,
        agent_repository: AgentRepository,
        usage_service: UsageService | None = None,
    ) -> None:
        self.uow = uow
        self.conversation_repository = conversation_repository
        self.agent_repository = agent_repository
        self.usage_service = usage_service

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
        # A live UI frame, sent once the return has committed: after the
        # request's commit, or -- inside a work item -- after the runtime's.
        self._publish_after_commit(
            conversation.id,
            [message_payload(paused_run_id, message_to_payload(saved_return))],
        )
        await self.uow.commit()
        return True

    def _publish_after_commit(
        self, conversation_id: UUID, frames: list[dict[str, object]]
    ) -> None:
        """Send live UI frames once this transaction commits, never inside it.

        Registered before the commit that carries them: inside a work item the
        commit is the runtime's (``RuntimeOwnedUnitOfWork``), and a Redis round
        trip inside a FAST handler's transaction is the one thing it may not do.
        """
        if not frames:
            return

        async def _publish() -> None:
            for frame in frames:
                await publish_conversation_event(conversation_id, frame)

        self.uow.after_commit(_publish)

    async def resume_would_duplicate_a_live_turn(
        self,
        paused_run_id: UUID,
        *,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> bool:
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
        call = await self.conversation_repository.get_tool_call(
            conversation_id=conversation_id, tool_call_id=tool_call_id
        )
        if call is None or not (call.metadata or {}).get(PARKED_IN_RUN_KEY):
            return False
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
        + ask_user in one assistant turn, or an ask_user alongside a wait_for).
        Resume only once every pausing call in the paused run is resolved —
        otherwise the unresolved sibling is orphaned (no return), dropped from the
        resumed run's history, and the agent re-asks it. The conversation lock
        serializes this so two near-simultaneous resolutions don't each start one.

        Not after Stop. An approval decided before Stop still finishes running
        its tool -- it cannot be taken back -- but the agent does not wake to
        carry on: Stop leaves the conversation STOPPED, and that is read here.
        """
        await self.conversation_repository.lock_conversation(conversation.id)
        remaining = await self.conversation_repository.unresolved_pausing_call_ids(
            conversation_id=conversation.id,
            agent_run_id=paused_run_id,
            pausing_tool_names=PAUSING_TOOL_NAMES,
        )
        if remaining:
            # Still paused on something else; that resolution will start the run.
            await self.uow.commit()
            return
        active_run = await self.conversation_repository.get_active_agent_run_for_update(
            conversation.id
        )
        if active_run is not None:
            # Another resolution already started the resume run, or the paused
            # run has not finished being written yet -- in which case its own
            # finalization resumes it (`resume_if_already_answered`).
            await self.uow.commit()
            return
        status = await self.conversation_repository.get_conversation_status(
            conversation.id
        )
        if status == ConversationStatus.STOPPED:
            await self.uow.commit()
            return
        await self._start_resume_run(
            conversation=conversation,
            paused_run_id=paused_run_id,
            resumed_tool_call_id=resumed_tool_call_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            source=source,
            publish_claimed=True,
        )

    async def resume_if_already_answered(
        self,
        *,
        conversation: Conversation,
        paused_run_id: UUID,
    ) -> tuple[UUID | None, list[Message]] | None:
        """Resume a run that finished pausing after its pause was answered.

        The pause is visible -- the card renders from the persisted call -- before
        the run that asked has been written as finished. An answer that lands in
        that gap finds the run still RUNNING and leaves the resume to it, and
        this is where it is picked up: once the run is written WAITING, if every
        pausing call it made already has its return, start the resume now.
        Nothing else would; the conversation sat WAITING on a question nobody
        was being asked any more.

        Otherwise, messages queued behind the run are marked as waiting on the
        answer and handed back for redrawing, so the person can see why nothing
        has replied to them. Returns the resume run's id (None when there is
        none) and the messages whose frames are owed, or None for nothing to do.
        """
        repository = self.conversation_repository
        await repository.lock_conversation(conversation.id)
        latest = await repository.get_latest_agent_run_for_conversation(conversation.id)
        if (
            latest is None
            or latest.id != paused_run_id
            or await repository.get_conversation_status(conversation.id)
            != ConversationStatus.WAITING
        ):
            # A newer run, or a resolution that already moved it on: either way
            # this run's pause is no longer the conversation's.
            await self.uow.commit()
            return None
        asked = await repository.pausing_call_ids_of_run(
            conversation_id=conversation.id,
            agent_run_id=paused_run_id,
            pausing_tool_names=PAUSING_TOOL_NAMES,
        )
        awaiting = {
            message.tool_call_id
            for message in await repository.pausing_calls_awaiting_a_return(
                conversation_id=conversation.id,
                pausing_tool_names=PAUSING_TOOL_NAMES,
            )
            if message.agent_run_id == paused_run_id
        }
        if asked and not awaiting:
            return await self._start_resume_run(
                conversation=conversation,
                paused_run_id=paused_run_id,
                resumed_tool_call_id=asked[-1],
                user_id=conversation.user_id,
                pod_id=conversation.pod_id,
                agent_name=None,
                source="approval_resume",
            )
        held = await repository.mark_queued_waiting_on_answer(paused_run_id)
        await self.uow.commit()
        return (None, held) if held else None

    async def _start_resume_run(
        self,
        *,
        conversation: Conversation,
        paused_run_id: UUID,
        resumed_tool_call_id: str,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
        source: str,
        publish_claimed: bool = False,
    ) -> ResumeStarted | None:
        """Create the resume run and claim what was said while it was paused.

        The caller holds the conversation lock and has established that nothing
        is running. Commits. None when the account is out of allowance: the
        answer stays recorded and the conversation stays WAITING, so the next
        message picks it up once there is headroom, and the person is told now.
        """
        agent = await resolve_agent(
            conversation,
            user_id=user_id,
            agent_repository=self.agent_repository,
        )
        selected_agent_runtime = await turn_runtime(self.uow, conversation, agent)
        try:
            # Resuming is a run like any other: skipping this made answering a
            # question a way past an exhausted limit. Inside a work item the
            # check is the turn's own reservation instead: a usage lookup has
            # no place in a FAST handler's transaction, and a turn refused
            # there fails with the same limit message.
            if not isinstance(self.uow, RuntimeOwnedUnitOfWork):
                await assert_usage_preflight_allowed(
                    self.usage_service,
                    organization_id=conversation.organization_id,
                    user_id=user_id,
                    agent_runtime=selected_agent_runtime,
                    outside_audience=Audience.of(conversation).answers_outsiders,
                )
        except UsageLimitExceededError as refused:
            self._report_refused_resume(
                conversation, paused_run_id=paused_run_id, refused=refused
            )
            await self.uow.commit()
            return None
        repository = self.conversation_repository
        paused_run = await repository.get_agent_run(paused_run_id)
        paused_metadata = paused_run.metadata if paused_run else None
        notes = run_is_private(paused_metadata)
        # A resume answers one kind, like a follow-up turn: the other kind
        # waits for the turn after it, which has to know where to look.
        leftovers = await repository.count_queued_user_messages(
            paused_run_id, notes=not notes
        )
        resume_run = await repository.create_agent_run(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            agent_runtime=selected_agent_runtime,
            metadata={
                "source": source,
                "resumed_tool_call_id": resumed_tool_call_id,
                # A note that paused for an approval finishes where it began.
                **privacy_of(paused_metadata),
                **({LEFTOVERS_KEY: [str(paused_run_id)]} if leftovers else {}),
            },
        )
        # What the person said while the pause was open is this run's to
        # answer: claimed into it, so a host carries it in the prompt and the
        # follow-up after this run does not answer it a second time.
        claimed = await repository.claim_queued_user_messages(
            paused_run_id, into_run_id=resume_run.id, notes=notes
        )
        await stage_agent_turn(
            self.uow,
            turn_for(
                conversation,
                run_id=resume_run.id,
                user_id=user_id,
                agent_name=agent_name,
            ),
        )
        if publish_claimed:
            self._publish_after_commit(
                conversation.id,
                [
                    message_payload(message.agent_run_id, message_to_payload(message))
                    for message in claimed
                ],
            )
        await self.uow.commit()
        return resume_run.id, claimed

    def _report_refused_resume(
        self,
        conversation: Conversation,
        *,
        paused_run_id: UUID,
        refused: UsageLimitExceededError,
    ) -> None:
        logger.warning(
            "agent.pause_resume.resume_refused_by_usage_limit.degraded",
            conversation_id=str(conversation.id),
            agent_run_id=str(paused_run_id),
        )
        self._publish_after_commit(
            conversation.id,
            [
                error_payload(
                    paused_run_id,
                    refused.message,
                    code=refused.code,
                    reason=refused.reason,
                )
            ],
        )
