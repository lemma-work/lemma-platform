"""Reading a conversation, and what the reader is allowed to see.

Every one of these is the same three steps -- resolve which agent the caller
means, load the row, check the caller may have it -- followed by the read
itself. They are together because that preamble is the interesting part: the
authorization is per-agent, not per-pod, so "list the conversations in this pod"
and "list this agent's conversations" are different questions with different
answers, and getting that boundary wrong is how one user sees another's thread.

The query side of the conversation use cases: one class, several reads, no
writes and no commits. It never starts a run.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.domain.errors import BadRequestError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.entities import AgentRun, Conversation, Message
from app.modules.agent.domain.ports import AgentRepository
from app.modules.agent.infrastructure import repositories
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.pauses import Awaiting, Pauses
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationAgentSelection,
    ConversationListCursor,
    ConversationStatus,
    ConversationType,
)
from app.modules.agent.domain.pauses import (
    ALL_KINDS,
    APPROVAL_KINDS,
    PERSON_KINDS,
    Pause,
)
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.pending_interactions import (
    PendingInteraction,
    PendingInteractionCursor,
)
from app.modules.agent.services.conversation_access import (
    authorized_conversation,
    require_agent_action,
    resolve_expected_agent_id,
)


class ConversationReads:
    """What a person may read of a conversation, and what it is waiting on."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        agent_repository: AgentRepository,
    ) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.runs = Runs(uow)
        self.messages = Messages(uow)
        self.pauses = Pauses(uow)
        self.agent_repository = agent_repository

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> ConversationReads:
        return cls(uow, repositories.AgentRepository(uow))

    async def list_conversations(
        self,
        *,
        pod_id: UUID,
        agent_selection: ConversationAgentSelection[str],
        user_id: UUID,
        status: ConversationStatus | None = None,
        type: ConversationType | None = None,
        metadata_filters: dict[str, object] | None = None,
        parent_id: UUID | None = None,
        archived: bool = False,
        search: str | None = None,
        cursor: ConversationListCursor | UUID | None = None,
        limit: int = 20,
        in_channel: bool | None = None,
    ) -> tuple[list[Conversation], ConversationListCursor | None]:
        """`cursor` may be a bare conversation id: a page token from before
        the list was ordered by activity. It continues after that row."""
        expected_agent_id = await resolve_expected_agent_id(
            self.agent_repository,
            pod_id=pod_id,
            agent_name=agent_selection.value,
        )
        resolved_selection = agent_selection.resolve(expected_agent_id)
        await require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
            action=Permissions.AGENT_READ,
        )
        if isinstance(cursor, UUID):
            cursor = await self.conversations.cursor_after(
                conversation_id=cursor, user_id=user_id, pod_id=pod_id
            )
            if cursor is None:
                raise BadRequestError("Invalid page_token")
        (
            conversations,
            next_cursor,
        ) = await self.conversations.list_conversations(
            user_id=user_id,
            pod_id=pod_id,
            agent_selection=resolved_selection,
            status=status,
            conversation_type=type,
            metadata_filters=metadata_filters,
            parent_id=parent_id,
            archived=archived,
            search=search,
            cursor=cursor,
            limit=limit,
            in_channel=in_channel,
        )
        # One query for the page rather than one per row. Not inferable from
        # the status: a WAITING conversation may be waiting on a timer, and an
        # Agent Host permission request waits inside a RUNNING one.
        waiting = (
            await self.pauses.conversations_waiting_on_person(
                [conversation.id for conversation in conversations]
            )
            if conversations
            else set()
        )
        for conversation in conversations:
            conversation.has_pending_interaction = conversation.id in waiting
        return conversations, next_cursor

    async def list_pending_interactions(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        cursor: PendingInteractionCursor | None = None,
        limit: int = 50,
    ) -> tuple[list[PendingInteraction], PendingInteractionCursor | None]:
        """Every pause waiting on this person in this pod, longest-waiting first.

        Authorized the way the pod-wide history list is, and scoped the same
        way: to conversations the caller owns, so nobody's pause is listed to
        anybody but the person it is waiting on.
        """
        await require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=None,
            action=Permissions.AGENT_READ,
        )
        return await self.pauses.waiting_on_person(
            user_id=user_id, pod_id=pod_id, cursor=cursor, limit=limit
        )

    async def get_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
        require_read_grant: bool = True,
    ) -> Conversation:
        # The latest run carries the failure diagnostics and the retry decision.
        # require_read_grant is False only for self-spawn (an agent operating on
        # its own conversation tree).
        conversation = await authorized_conversation(
            self.conversations,
            self.agent_repository,
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            action=Permissions.AGENT_READ,
            include_runs=True,
            require_grant=require_read_grant,
        )
        conversation.last_run_retryable = await self._latest_run_is_retryable(
            conversation
        )
        conversation.has_pending_interaction = conversation.id in (
            await self.pauses.conversations_waiting_on_person([conversation.id])
        )
        if conversation.status == ConversationStatus.WAITING:
            conversation.waiting_on_work = bool(
                await self.pauses.pending(conversation.id, kinds=(PauseKind.WAIT,))
            )
        return conversation

    async def _authorized(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
    ) -> Conversation:
        return await authorized_conversation(
            self.conversations,
            self.agent_repository,
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            action=Permissions.AGENT_READ,
        )

    async def _latest_run_is_retryable(self, conversation: Conversation) -> bool:
        """Whether the newest run can be replayed without duplicating output.

        The status check runs first and short-circuits: a run that did not fail
        is not retryable whatever its messages say, and that is nearly every
        conversation, so the message query below is rarely reached. Asking the
        database that question directly is what replaced eager-loading the
        whole transcript to evaluate it in Python.
        """
        latest = conversation.agent_runs[-1] if conversation.agent_runs else None
        if latest is None or latest.status != AgentRunStatus.FAILED:
            return False
        return await self.runs.run_is_replay_safe(latest.id)

    async def get_agent_run(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID,
        user_id: UUID,
        pod_id: UUID,
    ) -> AgentRun | None:
        """One run of a conversation the caller may read, or None when the run
        is not that conversation's."""
        conversation = await self.get_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
        )
        agent_run = await self.runs.get_agent_run(agent_run_id)
        if agent_run is None or agent_run.conversation_id != conversation.id:
            return None
        return agent_run

    async def list_messages(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
        before_sequence: int | None = None,
        after_sequence: int | None = None,
        limit: int = 100,
    ) -> tuple[list[Message], int | None]:
        await self._authorized(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        return await self.messages.list_messages(
            conversation_id=conversation_id,
            before_sequence=before_sequence,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def get_active_agent_run(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
    ) -> AgentRun | None:
        await self._authorized(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        return await self.runs.get_active_agent_run(conversation_id)

    async def list_user_approvals(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
    ) -> list[Message]:
        conversation = await self._authorized(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        # A pausing call stays listed until its synthesized return is durable --
        # the approved tool runs asynchronously, and a repeated click is how a
        # reconciliation the worker died during gets re-enqueued. Asked in SQL
        # rather than by scanning the newest 500 messages: a long agent run
        # writes hundreds of tool messages, and past that window the pending
        # approval simply disappeared from this list.
        pauses = await self.pauses.pending(
            conversation.id, kinds=PERSON_KINDS, awaiting=Awaiting.EITHER
        )
        return [pause.call for pause in pauses if pause.call is not None]

    async def pending_ask_user(self, *, conversation_id: UUID) -> Pause | None:
        """Oldest undecided ``ask_user``: what a surface renders as questions
        and routes a typed reply to."""
        return await self._oldest_undecided(conversation_id, {PauseKind.QUESTION})

    async def pending_approval(self, *, conversation_id: UUID) -> Pause | None:
        """Oldest undecided decision -- an approval, a budget, a permission.

        Its own lookup rather than a filter over
        :meth:`pending_user_interaction`: a conversation can hold several
        pauses, and an older ``ask_user`` nobody tapped would hide the card.
        """
        return await self._oldest_undecided(conversation_id, APPROVAL_KINDS)

    async def pending_sign_in(self, *, conversation_id: UUID) -> Pause | None:
        """Oldest undecided ``browser_sign_in``, for its sign-in link."""
        return await self._oldest_undecided(conversation_id, {PauseKind.SIGN_IN})

    async def pending_user_interaction(self, *, conversation_id: UUID) -> Pause | None:
        """Oldest undecided pause of any kind: where a typed reply goes."""
        return await self._oldest_undecided(conversation_id, ALL_KINDS)

    async def _oldest_undecided(
        self, conversation_id: UUID, kinds: Collection[PauseKind]
    ) -> Pause | None:
        pending = await self.pauses.pending(conversation_id, kinds=kinds, limit=1)
        return pending[0] if pending else None
