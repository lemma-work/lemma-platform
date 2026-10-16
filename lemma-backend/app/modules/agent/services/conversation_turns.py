"""Starting and stopping a turn.

A turn is one user message plus the run that answers it. Starting one is mostly
a question of *whether* to start a run at all -- a message typed while a run is
already going joins that run instead of racing a second one -- and the answer
has to be decided under the conversation lock, because two browser tabs asking
at once must not each get a run. A message can also wait behind a pause instead
(`turn_admission`). Stopping is in `conversation_stop`.

Split from `ConversationService` because this is the part with the lock, the
ordering constraints and the outbox; the rest of that class is storage.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.services.runtime_availability import turn_runtime
from app.modules.agent.services.usage_preflight import assert_usage_preflight_allowed
from app.modules.usage.contracts.execution import UsageService
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation, Message
from app.modules.agent.domain.private_notes import (
    PRIVATE_NOTE_KEY,
    run_metadata_for,
)
from app.modules.agent.domain.queued_messages import STOP_REQUESTED_AFTER_SEQUENCE
from app.modules.agent.domain.ports import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStartResult,
    AgentRunStatus,
    AgentRuntimeConfig,
    ConversationStatus,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.services.conversation_access import (
    require_agent_action,
    resolve_agent,
    validate_conversation_access,
)
from app.core.authorization.permissions import Permissions
from app.modules.agent.domain.errors import ConversationStateError
from app.modules.agent.infrastructure.queued_message_queries import (
    QueuedMessageRepository,
)
from app.modules.agent.services.conversation_approvals import ApprovalCoordinator
from app.modules.agent.services.conversation_stop import StopCoordinator
from app.modules.agent.services.pause_resume import PauseResume
from app.modules.agent.services.queued_followups import (
    LEFTOVERS_KEY,
    leftover_sources,
    next_queue,
    still_waiting,
)
from app.modules.agent.services.pod_runtime_defaults import (
    default_agent_runtime_for_pod,
)
from app.modules.agent.services.runtime_availability import (
    TurnRuntimeCheck,
    ensure_turn_runtime_available,
)
from app.modules.agent.services.realtime import (
    input_added_payload,
    message_payload,
    publish_conversation_event,
)
from app.modules.agent.contracts.turns import stage_agent_turn, turn_for
from app.modules.agent.services.run_dispatch import run_enqueue_suppressed
from app.modules.agent.services.turn_admission import (
    HeldTurn,
    hold_behind,
    pause_holding_the_turn,
    stamped,
    user_message_metadata,
)
from app.modules.agent.services.serialization import message_to_payload

logger = get_logger(__name__)


def _owed_an_answer(
    completed_run: AgentRun | None, completed_run_id: UUID
) -> tuple[list[UUID], int | None] | None:
    """Where to look for what is owed an answer, and from which sequence on.

    After Stop only the stopped run, and only what was said after Stop; None
    when the stop left no mark to say where that is. Otherwise the run and
    whatever earlier follow-ups left waiting behind it.
    """
    if completed_run is not None and completed_run.status == AgentRunStatus.STOPPED:
        stopped_after = _said_after_stop(completed_run)
        return None if stopped_after is None else ([completed_run_id], stopped_after)
    return [
        completed_run_id,
        *leftover_sources(completed_run.metadata if completed_run else None),
    ], None


def _said_after_stop(run: AgentRun | None) -> int | None:
    """The newest message sequence when Stop was pressed on this run.

    None for a stop that did not record one, which is answered as Stop always
    was: nothing starts.
    """
    value = (run.metadata or {}).get(STOP_REQUESTED_AFTER_SEQUENCE) if run else None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


class TurnCoordinator:
    """Starts the run that answers a message, and stops the one in flight."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        conversation_repository: ConversationRepository,
        agent_repository: AgentRepository,
        approvals: ApprovalCoordinator,
        pauses: PauseResume,
        usage_service: UsageService | None,
        runtime_check: TurnRuntimeCheck = ensure_turn_runtime_available,
    ) -> None:
        self.uow = uow
        self.conversation_repository = conversation_repository
        self.agent_repository = agent_repository
        self.approvals = approvals
        self.pauses = pauses
        self.usage_service = usage_service
        self.runtime_check = runtime_check
        self.stops = StopCoordinator(
            uow, conversation_repository, agent_repository, approvals, pauses
        )

    async def start(
        self,
        conversation: Conversation,
        *,
        user_id: UUID,
        pod_id: UUID,
        content: str,
        agent_name: str | None,
        message_metadata: dict[str, object] | None,
        typed_in_lemma: bool = False,
        client_message_id: str | None = None,
        supersede_pauses: bool = True,
    ) -> AgentRunStartResult:
        """Append the message and return the run that will answer it.

        The conversation is already loaded and access-checked by the caller --
        this is the half that needs the lock. ``typed_in_lemma`` is the caller
        saying a person typed this in Lemma's own composer, rather than a chat
        platform, a sub-agent or a reply delivery handing it on.

        ``client_message_id`` is the sender's id for this send: a second send
        with the same id is the first one retried, and gets the first one's run
        back rather than a second message. ``supersede_pauses`` is False for a
        sender who is not the person a pause asked (``turn_admission``).
        """
        message_metadata = stamped(
            message_metadata, conversation, typed_in_lemma=typed_in_lemma
        )
        # Resolve the agent (a read) before taking the conversation lock, so the
        # FOR UPDATE span covers only the active-run check + run/message writes.
        agent = await resolve_agent(
            conversation,
            user_id=user_id,
            agent_repository=self.agent_repository,
        )

        await self.conversation_repository.lock_conversation(conversation.id)
        if client_message_id is not None:
            # Under the lock, so two copies of one send cannot both miss it.
            original = (
                await self.conversation_repository.find_user_message_by_client_id(
                    conversation_id=conversation.id, client_message_id=client_message_id
                )
            )
            if original is not None and original.agent_run_id is not None:
                await self.uow.commit()
                return AgentRunStartResult(
                    conversation_id=conversation.id,
                    agent_run_id=original.agent_run_id,
                    started_new_run=False,
                    deduplicated=True,
                )
        active_run = await self.conversation_repository.get_active_agent_run_for_update(
            conversation.id
        )
        superseded_returns: list[Message] = []
        held: HeldTurn | None = None
        if active_run is None:
            # A prior run may have paused on ask_user/request_approval (conversation
            # -> WAITING) without the user ever resolving it — the composer stays
            # enabled during WAITING, so the user can type past the card. Deny any
            # such leftover call now: otherwise this new run's history rebuild finds
            # no matching return for it and silently drops it (see
            # PydanticAIHarness._build_tool_batch), permanently losing the model's
            # memory of asking and leaving the UI card stuck "needs your input".
            if supersede_pauses:
                superseded_returns = (
                    await self.approvals.supersede_stale_pending_interactions(
                        conversation=conversation,
                        user_id=user_id,
                    )
                )
            held = await pause_holding_the_turn(
                self.conversation_repository,
                conversation.id,
                include_undecided=not supersede_pauses,
            )
        started_new_run = active_run is None and held is None
        if started_new_run:
            active_run = await self._create_run(
                conversation, agent, user_id=user_id, message_metadata=message_metadata
            )
        answering_run_id = active_run.id if active_run is not None else None
        if held is not None:
            answering_run_id = held.agent_run_id
            await hold_behind(self.conversation_repository, conversation)
        assert answering_run_id is not None

        saved_user_message = await self.conversation_repository.append_message(
            conversation_id=conversation.id,
            agent_run_id=answering_run_id,
            draft=MessageDraft.of_text(
                content,
                role=MessageRole.USER,
                metadata=user_message_metadata(
                    message_metadata,
                    conversation,
                    joins_a_run=not started_new_run,
                    client_message_id=client_message_id,
                ),
            ),
        )

        if started_new_run:
            # The turn commits with the run and its message, or not at all.
            await stage_agent_turn(
                self.uow,
                turn_for(
                    conversation,
                    run_id=answering_run_id,
                    user_id=user_id,
                    agent_name=agent_name,
                ),
            )

        # Streaming endpoints need the message/run and its outbox event committed
        # atomically before the worker can safely load them; normal CRUD methods
        # still rely on the request UoW.
        await self.uow.commit()
        # After the commit, not inside it: this claimed to run "now that they're
        # durably committed", but the commit is the caller's, so it held a
        # connection across a Redis round trip with the row locked. Not the
        # outbox -- these are live UI frames; the next fetch recovers a lost one.
        frames = [
            message_payload(item.agent_run_id, message_to_payload(item))
            for item in superseded_returns
        ] + [
            input_added_payload(
                answering_run_id, message_to_payload(saved_user_message)
            )
        ]

        async def _publish_frames() -> None:
            for frame in frames:
                await publish_conversation_event(conversation.id, frame)

        self.uow.after_commit(_publish_frames)
        return AgentRunStartResult(
            conversation_id=conversation.id,
            agent_run_id=answering_run_id,
            started_new_run=started_new_run,
        )

    async def _create_run(
        self,
        conversation: Conversation,
        agent: Agent,
        *,
        user_id: UUID,
        message_metadata: dict[str, object] | None,
    ) -> AgentRun:
        selected_agent_runtime = await turn_runtime(self.uow, conversation, agent)
        await self.runtime_check(
            self.uow,
            selected_agent_runtime,
            organization_id=conversation.organization_id,
            user_id=user_id,
        )
        await self.assert_usage_preflight_allowed(
            organization_id=conversation.organization_id,
            user_id=user_id,
            agent_runtime=selected_agent_runtime,
        )
        return await self.conversation_repository.create_agent_run(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            agent_runtime=selected_agent_runtime,
            metadata=run_metadata_for(message_metadata),
        )

    async def start_queued_followup(
        self,
        *,
        conversation: Conversation,
        completed_run_id: UUID,
    ) -> tuple[UUID | None, list[Message]] | None:
        """The backstop for messages no run ever read.

        Normally nothing reaches here. A message sent while a run is in flight is
        steered into that run -- by `PendingUserMessagesCapability` in process,
        and by a `STEER_RUN` for an Agent Host harness that advertises ACP
        steering -- and whoever delivers it claims it, so by the time the run
        ends there is nothing left owing. Three cases still get past that, and
        each leaves a person waiting on an answer that will otherwise never
        come:

        - **An Agent Host harness that cannot steer.** ACP gives a running
          `session/prompt` no way to take more input, so the message waits for
          the turn to end and this run delivers it.
        - **A steer that did not land**, because the turn ended first.
        - **A run that died before draining**, so the messages are still
          unclaimed.

        Returns the new run's id and the messages whose live UI frames the
        caller owes the conversation -- superseded pause returns, and the queued
        messages this run just claimed -- or None when there is nothing to
        answer. After a pause the run id can be None with frames still owed:
        the queued messages, redrawn as waiting on the person's answer. A
        stopped run answers only what was said after Stop. The frames come back rather than going out from here because
        publishing is a Redis round trip and this still holds a pooled
        connection -- and unlike `start`, whose caller commits again later and
        drains `after_commit`, the caller here is a worker job with no second
        commit to hang them on.

        This cannot recur: the queued messages belong to ``completed_run_id``,
        never to the run started here, so the run started here has an empty
        queue of its own unless the person types during it too -- which is the
        case that should chain.
        """
        if run_enqueue_suppressed():
            # The caller executes runs itself and only asked for the ones it
            # started. Creating one here would leave a RUNNING row nobody runs.
            return None
        if conversation.status == ConversationStatus.WAITING:
            # The run ended by asking the person something. Starting a turn now
            # would auto-deny that question before they had a chance to see it,
            # and the queue does not need it: the run that resumes the pause
            # claims these messages. Unless it has been answered already.
            return await self.pauses.resume_if_already_answered(
                conversation=conversation, paused_run_id=completed_run_id
            )
        completed_run = await self.conversation_repository.get_agent_run(
            completed_run_id
        )
        owed = _owed_an_answer(completed_run, completed_run_id)
        if owed is None:
            # Stop means stop: nothing said before it starts the next turn.
            return None
        sources, stopped_after = owed
        queue = await next_queue(
            self.conversation_repository, sources, after_sequence=stopped_after
        )
        if queue is None:
            return None
        answering, notes = queue

        await self.conversation_repository.lock_conversation(conversation.id)
        if await self.conversation_repository.get_active_agent_run_for_update(
            conversation.id
        ):
            # Someone typed between that run finishing and this check. Their
            # message started a turn whose history already covers the queue.
            return None

        # Same reason as in `start`: a pausing call left unresolved by the run
        # that just ended would have no matching return in the next run's
        # history rebuild, and would be dropped along with the model's memory of
        # having asked.
        superseded_returns = await self.approvals.supersede_stale_pending_interactions(
            conversation=conversation,
            user_id=conversation.user_id,
        )
        leftovers = await still_waiting(
            self.conversation_repository,
            sources,
            answering=answering,
            notes=notes,
            after_sequence=stopped_after,
        )
        agent_runtime = (
            (completed_run.agent_runtime if completed_run is not None else None)
            or conversation.agent_runtime
            or await default_agent_runtime_for_pod(self.uow, pod_id=conversation.pod_id)
        )
        # Continuing an unanswered message is still a run someone pays for.
        await self.assert_usage_preflight_allowed(
            organization_id=conversation.organization_id,
            user_id=conversation.user_id,
            agent_runtime=agent_runtime,
        )
        followup_run = await self.conversation_repository.create_agent_run(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            agent_runtime=agent_runtime,
            metadata={
                "source": "queued_messages",
                "queued_behind_agent_run_id": str(answering),
                # One kind per run: a note's answer stays in Lemma.
                **({PRIVATE_NOTE_KEY: True} if notes else {}),
                **({LEFTOVERS_KEY: leftovers} if leftovers else {}),
            },
        )
        # Claimed by the run that will answer them, in the same transaction
        # that creates it. That is what tells an Agent Host dispatch which
        # messages its prompt has to carry -- all of them, not only the newest
        # -- and what tells the person's client they are no longer waiting.
        claimed = await self.conversation_repository.claim_queued_user_messages(
            answering,
            into_run_id=followup_run.id,
            notes=notes,
            after_sequence=stopped_after,
        )
        # Resolved from the conversation, as it is for every run nobody named
        # an agent for.
        await stage_agent_turn(
            self.uow,
            turn_for(
                conversation, run_id=followup_run.id, user_id=conversation.user_id
            ),
        )
        await self.uow.commit()
        return followup_run.id, [*superseded_returns, *claimed]

    async def stop_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
    ) -> Conversation:
        """Stop the run in flight, or the pause it left; see `conversation_stop`."""
        return await self.stops.stop_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )

    async def withdraw_queued_message(
        self,
        *,
        conversation_id: UUID,
        message_id: UUID,
        user_id: UUID,
        pod_id: UUID,
    ) -> None:
        """Take back a message the agent has not seen yet.

        Only a queued message nobody is carrying: once a run has claimed it, or
        a steer is on its way to a host, it may already be in the agent's
        context, and deleting it would leave the agent answering something the
        person can no longer see. That is a conflict, not a missing message --
        the client drew it as queued a moment ago and should redraw it.
        """
        conversation = validate_conversation_access(
            await self.conversation_repository.get_conversation(conversation_id),
            user_id=user_id,
            pod_id=pod_id,
        )
        await require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=conversation.agent_id,
            action=Permissions.AGENT_EXECUTE,
        )
        withdrawn = await QueuedMessageRepository(
            self.uow
        ).withdraw_queued_user_message(
            conversation_id=conversation.id, message_id=message_id
        )
        if not withdrawn:
            raise ConversationStateError(
                "This message is no longer waiting: the agent already has it."
            )
        await self.uow.commit()

    async def assert_usage_preflight_allowed(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
        agent_runtime: AgentRuntimeConfig,
    ) -> None:
        """Refuse to start a run the account has no headroom for.

        Public because starting a turn is not the only way a run begins:
        `ConversationRetryService.retry_failed_run` starts one too, and reaches
        it through `ConversationService.turns`. See `usage_preflight`.
        """
        await assert_usage_preflight_allowed(
            self.usage_service,
            organization_id=organization_id,
            user_id=user_id,
            agent_runtime=agent_runtime,
        )
