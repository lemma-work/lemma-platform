"""The follow-up turn: answering what nobody read while a run was going.

A follow-up answers what was said while the run ran. Some of that may be private
notes, and a run answers one kind: a note's answer stays in Lemma, and anything
else goes to the platform (``domain/private_notes``). So a follow-up takes the
kind of the oldest message waiting and leaves the other kind for the next one --
recording where it waits (``queued_leftovers_behind``), because it is queued
behind a run that has already finished, and nothing else would look there again.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.application.pauses.supersede import SupersedeStalePauses
from app.modules.agent.contracts.turns import stage_agent_turn, turn_for
from app.modules.agent.domain.entities import AgentRun, Conversation, Message
from app.modules.agent.domain.ports import AgentRepository
from app.modules.agent.domain.run_state import RunState
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    RunTrigger,
)
from app.modules.agent.infrastructure import repositories
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.pauses import Awaiting, Pauses
from app.modules.agent.infrastructure.db.queued_messages import QueuedMessages
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.pause_resume import start_resume_run
from app.modules.agent.services.runtime_resolution import (
    default_agent_runtime_for_pod,
)
from app.modules.agent.services.run_dispatch import run_enqueue_suppressed
from app.modules.agent.services.usage_preflight import assert_turn_allowed
from app.modules.usage.contracts.execution import UsageService, build_usage_service

#: The new run's id (None after a pause: nothing started) and the messages whose
#: live UI frames the caller owes the conversation.
FollowUp = tuple[UUID | None, list[Message]]


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
        *(completed_run.state.queued_leftovers_behind if completed_run else ()),
    ], None


def _said_after_stop(run: AgentRun | None) -> int | None:
    """The newest message sequence when Stop was pressed on this run.

    None for a stop that did not record one, which is answered as Stop always
    was: nothing starts.
    """
    return run.state.stop_requested_after_sequence if run else None


async def _next_queue(
    repository: QueuedMessages,
    sources: list[UUID],
    *,
    after_sequence: int | None = None,
) -> tuple[UUID, bool] | None:
    """The run whose queue to answer, and whether its oldest message is a note.

    ``after_sequence`` keeps to what was said after that point (after Stop).
    """
    for source in sources:
        notes = await repository.earliest_queued_is_note(
            source, after_sequence=after_sequence
        )
        if notes is not None:
            return source, notes
    return None


async def _still_waiting(
    repository: QueuedMessages,
    sources: list[UUID],
    *,
    answering: UUID,
    notes: bool,
    after_sequence: int | None = None,
) -> list[UUID]:
    """The sources that will still hold unanswered messages once this one is claimed."""
    waiting: list[UUID] = []
    for source in sources:
        left = await repository.count_queued_user_messages(
            source,
            notes=(not notes) if source == answering else None,
            after_sequence=after_sequence,
        )
        if left:
            waiting.append(source)
    return waiting


class StartQueuedFollowUp:
    """Starts the run owed for what was said while the last one ran."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        agent_repository: AgentRepository,
        supersede: SupersedeStalePauses,
        usage_service: UsageService | None,
    ) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.runs = Runs(uow)
        self.queued_messages = QueuedMessages(uow)
        self.pauses = Pauses(uow)
        self.agent_repository = agent_repository
        self.supersede = supersede
        self.usage_service = usage_service

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> StartQueuedFollowUp:
        return cls(
            uow,
            agent_repository=repositories.AgentRepository(uow),
            supersede=SupersedeStalePauses.build(uow),
            usage_service=build_usage_service(uow),
        )

    async def start(
        self,
        *,
        conversation: Conversation,
        completed_run_id: UUID,
    ) -> FollowUp | None:
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
        stopped run answers only what was said after Stop. The frames come back
        rather than going out from here because publishing is a Redis round
        trip and this still holds a pooled connection -- and the caller is a
        worker job with no second commit to hang them on.

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
            return await self._resume_if_already_answered(
                conversation=conversation, paused_run_id=completed_run_id
            )
        completed_run = await self.runs.get_agent_run(completed_run_id)
        owed = _owed_an_answer(completed_run, completed_run_id)
        if owed is None:
            # Stop means stop: nothing said before it starts the next turn.
            return None
        sources, stopped_after = owed
        queue = await _next_queue(
            self.queued_messages, sources, after_sequence=stopped_after
        )
        if queue is None:
            return None
        answering, notes = queue

        await self.conversations.lock_conversation(conversation.id)
        if await self.runs.get_active_agent_run_for_update(conversation.id):
            # Someone typed between that run finishing and this check. Their
            # message started a turn whose history already covers the queue.
            return None

        # Same reason as in `AdmitMessage`: a pausing call left unresolved by
        # the run that just ended would have no matching return in the next
        # run's history rebuild, and would be dropped along with the model's
        # memory of having asked.
        superseded_returns = await self.supersede.supersede(
            conversation=conversation,
            user_id=conversation.user_id,
        )
        leftovers = await _still_waiting(
            self.queued_messages,
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
        await assert_turn_allowed(
            self.usage_service,
            conversation,
            user_id=conversation.user_id,
            agent_runtime=agent_runtime,
        )
        followup_run = await self.runs.create_agent_run(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            agent_runtime=agent_runtime,
            state=RunState(
                trigger=RunTrigger.FOLLOW_UP,
                queued_behind_run_id=answering,
                # One kind per run: a note's answer stays in Lemma.
                private_note=notes,
                queued_leftovers_behind=leftovers,
            ),
        )
        # Claimed by the run that will answer them, in the same transaction
        # that creates it. That is what tells an Agent Host dispatch which
        # messages its prompt has to carry -- all of them, not only the newest
        # -- and what tells the person's client they are no longer waiting.
        claimed = await self.queued_messages.claim_queued_user_messages(
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

    async def _resume_if_already_answered(
        self,
        *,
        conversation: Conversation,
        paused_run_id: UUID,
    ) -> FollowUp | None:
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
        await self.conversations.lock_conversation(conversation.id)
        latest = await self.runs.get_latest_agent_run_for_conversation(conversation.id)
        if (
            latest is None
            or latest.id != paused_run_id
            or await self.conversations.get_conversation_status(conversation.id)
            != ConversationStatus.WAITING
        ):
            # A newer run, or a resolution that already moved it on: either way
            # this run's pause is no longer the conversation's.
            await self.uow.commit()
            return None
        asked = await self.pauses.pending(
            conversation.id, run_id=paused_run_id, awaiting=Awaiting.ANY
        )
        awaiting = await self.pauses.pending(
            conversation.id, run_id=paused_run_id, awaiting=Awaiting.EITHER
        )
        if asked and not awaiting:
            return await start_resume_run(
                self.uow,
                runs=self.runs,
                queued_messages=self.queued_messages,
                agent_repository=self.agent_repository,
                usage_service=self.usage_service,
                conversation=conversation,
                paused_run_id=paused_run_id,
                resumed_tool_call_id=asked[-1].tool_call_id,
                user_id=conversation.user_id,
                agent_name=None,
                trigger=RunTrigger.RESUME,
            )
        held = await self.queued_messages.mark_queued_waiting_on_answer(paused_run_id)
        await self.uow.commit()
        return (None, held) if held else None
