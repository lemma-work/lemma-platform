"""Admitting a message into a loaded conversation: start a run, join one, or wait.

A turn is one user message plus the run that answers it. Admitting one is mostly
a question of *whether* to start a run at all -- a message typed while a run is
already going joins that run instead of racing a second one -- and the answer
has to be decided under the conversation lock, because two browser tabs asking
at once must not each get a run. A message can also wait behind a pause instead
(``domain/turn_admission``).

The half of sending that needs the lock, the ordering constraints and the
outbox. ``SendMessage`` finds or opens the conversation and authorizes the
sender, then hands on to this; deliveries that already hold a loaded, owned
conversation -- a sub-agent's result, a pod's answer, replies to the agent's
own messages -- call it directly.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.application.pauses.supersede import SupersedeStalePauses
from app.modules.agent.contracts.turns import stage_agent_turn, turn_for
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation, Message
from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.pauses import PERSON_KINDS
from app.modules.agent.domain.ports import AgentRepository
from app.modules.agent.domain.private_notes import (
    requested_private_note,
    run_state_for,
)
from app.modules.agent.domain.run_state import RunState
from app.modules.agent.domain.turn_admission import (
    HeldTurn,
    admitted_state,
    message_source,
    user_message_metadata,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStartResult,
    ConversationStatus,
    MessageDraft,
    MessageRole,
    QueueState,
)
from app.modules.agent.infrastructure import repositories
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.pauses import Awaiting, Pauses
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.conversation_access import resolve_agent
from app.modules.agent.infrastructure.live import publish_conversation_event
from app.modules.agent.domain.live_events import input_added_payload, message_payload
from app.modules.agent.services.runtime_availability import (
    TurnRuntimeCheck,
    ensure_turn_runtime_available,
    turn_runtime,
)
from app.modules.agent.domain.live_events import message_to_payload
from app.modules.agent.services.surface_context import (
    SurfaceKindReader,
    surface_kind,
)
from app.modules.agent.services.usage_preflight import assert_turn_allowed
from app.modules.usage.contracts.execution import UsageService, build_usage_service


def _queued_unless(started_new_run: bool, state: MessageState) -> MessageState:
    """A message that did not start its run joins one already going, which
    loaded its history before the message existed: it is queued."""
    if started_new_run:
        return state
    return state.model_copy(update={"queue_state": QueueState.QUEUED})


async def _pause_holding_the_turn(
    repository: Pauses,
    conversation_id: UUID,
    *,
    include_undecided: bool,
) -> HeldTurn | None:
    """The pause a message must wait behind, if any. Caller holds the lock."""
    holding = await repository.pending(
        conversation_id,
        kinds=PERSON_KINDS,
        awaiting=Awaiting.EITHER if include_undecided else Awaiting.RETURN,
    )
    return HeldTurn(agent_run_id=holding[-1].run_id) if holding else None


async def _hold_behind(repository: Conversations, conversation: Conversation) -> None:
    """Leave the conversation waiting on the pause the message is held behind.

    WAITING even after Stop: Stop keeps an already-decided approval from waking
    the agent, but a message sent afterwards asks for an answer, and the resume
    is what will carry it. Nothing has to be re-queued for the decided ones:
    each decision wrote its ``agent.approval.execute`` item in its own
    transaction, and that item always ends with a return.
    """
    if conversation.status != ConversationStatus.WAITING:
        await repository.set_conversation_status(
            conversation_id=conversation.id, status=ConversationStatus.WAITING
        )
        conversation.status = ConversationStatus.WAITING


class AdmitMessage:
    """Appends a message and returns the run that will answer it."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        agent_repository: AgentRepository,
        supersede: SupersedeStalePauses,
        usage_service: UsageService | None,
        runtime_check: TurnRuntimeCheck = ensure_turn_runtime_available,
        surface_kind: SurfaceKindReader = surface_kind,
    ) -> None:
        self.uow = uow
        self.surface_kind = surface_kind
        self.conversations = Conversations(uow)
        self.runs = Runs(uow)
        self.messages = Messages(uow)
        self.pauses = Pauses(uow)
        self.agent_repository = agent_repository
        self.supersede = supersede
        self.usage_service = usage_service
        self.runtime_check = runtime_check

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> AdmitMessage:
        return cls(
            uow,
            agent_repository=repositories.AgentRepository(uow),
            supersede=SupersedeStalePauses.build(uow),
            usage_service=build_usage_service(uow),
        )

    async def admit(
        self,
        conversation: Conversation,
        *,
        user_id: UUID,
        content: str,
        agent_name: str | None,
        message_metadata: dict[str, object] | None,
        typed_in_lemma: bool = False,
        client_message_id: str | None = None,
        supersede_pauses: bool = True,
    ) -> AgentRunStartResult:
        """Append the message and return the run that will answer it. Commits.

        The conversation is already loaded and access-checked by the caller --
        this is the half that needs the lock. ``typed_in_lemma`` is the caller
        saying a person typed this in Lemma's own composer, rather than a chat
        platform, a sub-agent or a reply delivery handing it on.

        ``client_message_id`` is the sender's id for this send: a second send
        with the same id is the first one retried, and gets the first one's run
        back rather than a second message. ``supersede_pauses`` is False for a
        sender who is not the person a pause asked (``domain/turn_admission``).
        """
        message_state = admitted_state(
            message_metadata,
            conversation,
            # Only a message typed in Lemma, or a note, is marked by where it is.
            surface_kind=await self.surface_kind(self.uow, conversation.id)
            if typed_in_lemma or requested_private_note(message_metadata)
            else None,
            typed_in_lemma=typed_in_lemma,
            client_message_id=client_message_id,
        )
        # Resolve the agent (a read) before taking the conversation lock, so the
        # FOR UPDATE span covers only the active-run check + run/message writes.
        agent = await resolve_agent(
            conversation,
            user_id=user_id,
            agent_repository=self.agent_repository,
        )

        await self.conversations.lock_conversation(conversation.id)
        if client_message_id is not None:
            # Under the lock, so two copies of one send cannot both miss it.
            original = await self.messages.find_user_message_by_client_id(
                conversation_id=conversation.id, client_message_id=client_message_id
            )
            if original is not None and original.agent_run_id is not None:
                await self.uow.commit()
                return AgentRunStartResult(
                    conversation_id=conversation.id,
                    agent_run_id=original.agent_run_id,
                    started_new_run=False,
                    deduplicated=True,
                )
        active_run = await self.runs.get_active_agent_run_for_update(conversation.id)
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
                superseded_returns = await self.supersede.supersede(
                    conversation=conversation,
                    user_id=user_id,
                )
            held = await _pause_holding_the_turn(
                self.pauses,
                conversation.id,
                include_undecided=not supersede_pauses,
            )
        started_new_run = active_run is None and held is None
        if started_new_run:
            active_run = await self._create_run(
                conversation,
                agent,
                user_id=user_id,
                state=run_state_for(
                    message_state, message_source=message_source(message_metadata)
                ),
            )
        answering_run_id = active_run.id if active_run is not None else None
        if held is not None:
            answering_run_id = held.agent_run_id
            await _hold_behind(self.conversations, conversation)
        assert answering_run_id is not None

        saved_user_message = await self.messages.append_message(
            conversation_id=conversation.id,
            agent_run_id=answering_run_id,
            draft=MessageDraft.of_text(
                content,
                role=MessageRole.USER,
                metadata=user_message_metadata(message_metadata),
                state=_queued_unless(started_new_run, message_state),
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
        state: RunState,
    ) -> AgentRun:
        selected_agent_runtime = await turn_runtime(self.uow, conversation, agent)
        await self.runtime_check(
            self.uow,
            selected_agent_runtime,
            organization_id=conversation.organization_id,
            user_id=user_id,
        )
        await assert_turn_allowed(
            self.usage_service,
            conversation,
            user_id=user_id,
            agent_runtime=selected_agent_runtime,
        )
        return await self.runs.create_agent_run(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            agent_runtime=selected_agent_runtime,
            state=state,
        )
