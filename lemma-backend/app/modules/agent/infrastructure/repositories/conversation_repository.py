"""Conversations, their runs, and the messages that make them up."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, literal, literal_column, select, update
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.orm import selectinload

from app.core.authorization.delegation import DEFAULT_POD_AGENT_ID
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.events import (
    AgentDomainEvent,
    ConversationStartedEvent,
)
from app.modules.agent.domain.entities import (
    AgentRun as AgentRunEntity,
    Conversation as ConversationEntity,
    Message as MessageEntity,
)
from app.modules.agent.domain.value_objects import (
    TERMINAL_AGENT_RUN_STATUSES,
    AgentRuntimeConfig,
    AgentRunFinishResult,
    AgentRunStatus,
    ConversationAgentScope,
    ConversationAgentSelection,
    ConversationStatus,
    ConversationType,
    JsonObject,
    JsonValue,
    MessageDraft,
    to_json_value,
)
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from app.modules.agent.infrastructure.conversation_origin_store import (
    create_conversation_for_origin,
)
from app.modules.agent.infrastructure.conversation_run_queries import (
    ConversationRunQueriesMixin,
)
from app.modules.agent.infrastructure.repository_status import (
    conversation_status_values_for_db as _conversation_status_values_for_db,
)


from app.modules.agent.infrastructure.repositories.conversation_status_repair import (
    reconcile_conversation_to_terminal,
)
from app.modules.agent.infrastructure.repositories.conversation_approval_queries import (
    ConversationApprovalQueriesMixin,
)
from app.modules.agent.infrastructure.repositories.conversation_opening_texts import (
    ConversationOpeningTextsMixin,
)

_DEFAULT_POD_AGENT_ID_SQL = literal_column(f"'{DEFAULT_POD_AGENT_ID}'::uuid")


class ConversationRepository(
    ConversationApprovalQueriesMixin,
    ConversationOpeningTextsMixin,
    ConversationRunQueriesMixin,
):
    """Repository for conversations, agent runs, and messages."""

    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.session = uow.session

    def collect_events(self, events: Sequence[AgentDomainEvent]) -> None:
        self.uow.collect_events(events)

    async def create_conversation(
        self,
        conversation: ConversationEntity,
    ) -> ConversationEntity:
        model = ConversationModel(
            id=conversation.id,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
            organization_id=conversation.organization_id,
            agent_id=conversation.agent_id,
            title=conversation.title,
            instructions=conversation.instructions,
            agent_runtime=(
                conversation.agent_runtime.model_dump(mode="json")
                if conversation.agent_runtime
                else None
            ),
            origin_type=conversation.origin_type,
            origin_id=conversation.origin_id,
            conversation_type=conversation.type.value,
            status=conversation.status.value if conversation.status else None,
            output_data=conversation.output,
            parent_id=conversation.parent_id,
            conversation_metadata=conversation.metadata,
            is_archived=conversation.is_archived,
        )
        self.session.add(model)
        await self.session.flush()
        self.uow.collect_events(
            [
                ConversationStartedEvent(
                    conversation_id=model.id,
                    pod_id=model.pod_id,
                    user_id=model.user_id,
                    agent_id=model.agent_id,
                    parent_id=model.parent_id,
                )
            ]
        )
        return model.to_entity()

    async def create_conversation_once(
        self,
        conversation: ConversationEntity,
    ) -> tuple[ConversationEntity, bool]:
        """Create one conversation for a durable external invocation origin."""
        return await create_conversation_for_origin(self.session, conversation)

    async def update_conversation(
        self,
        conversation: ConversationEntity,
    ) -> ConversationEntity:
        result = await self.session.execute(
            select(ConversationModel).where(ConversationModel.id == conversation.id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return conversation

        model.title = conversation.title
        model.instructions = conversation.instructions
        model.agent_runtime = (
            conversation.agent_runtime.model_dump(mode="json")
            if conversation.agent_runtime
            else None
        )
        model.conversation_type = conversation.type.value
        model.status = conversation.status.value if conversation.status else None
        model.output_data = conversation.output
        model.conversation_metadata = conversation.metadata
        model.is_archived = conversation.is_archived
        await self.session.flush()
        return model.to_entity()

    async def get_conversation_metadata_key(
        self,
        conversation_id: UUID,
        key: str,
    ) -> JsonValue | None:
        """Read a single key out of a conversation's metadata JSON blob."""
        result = await self.session.execute(
            select(ConversationModel.conversation_metadata).where(
                ConversationModel.id == conversation_id
            )
        )
        metadata = result.scalar_one_or_none()
        if not isinstance(metadata, dict):
            return None
        return metadata.get(key)

    async def set_conversation_metadata_key(
        self,
        conversation_id: UUID,
        key: str,
        value: JsonValue,
    ) -> None:
        """Write a single metadata key without clobbering sibling keys.

        Uses ``jsonb_set`` so concurrent writers touching other keys (e.g. the
        ``is_sub_agent`` / ``surface_platform`` flags) are never overwritten.

        ``value`` must be bound with the ``JSONB`` type directly (``literal``,
        not ``cast(json.dumps(value), JSONB)``) — casting an already-serialized
        JSON string double-encodes it, so ``jsonb_set`` stores a JSON *string*
        scalar (the dumped text) instead of the intended array/object.
        """
        stmt = (
            update(ConversationModel)
            .where(ConversationModel.id == conversation_id)
            .values(
                conversation_metadata=func.jsonb_set(
                    func.coalesce(
                        ConversationModel.conversation_metadata,
                        literal({}, JSONB),
                    ),
                    array([key]),
                    literal(value, JSONB),
                    True,
                )
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def get_conversation(
        self,
        conversation_id: UUID,
        *,
        include_messages: bool = False,
        include_runs: bool = False,
    ) -> ConversationEntity | None:
        """Load a conversation, optionally with its messages and its latest run.

        ``include_runs`` used to eager-load every run of the conversation and
        every message of every run. Nothing consumed that: the entity derives
        four scalars from ``agent_runs[-1]``, and the three callers that pass
        this flag read the latest run or nothing at all. On a long-lived thread
        it meant re-reading the whole transcript — the worst conversation in
        production carries 13,688 messages — to answer a question about one row.

        Now it fetches exactly that one run, through the index that already
        exists for it. ``agent_runs`` still carries a single element so
        ``[-1]`` keeps working for callers, and ``to_entity()`` derives
        ``status`` and the ``last_run_*`` fields from it unchanged.
        """
        stmt = select(ConversationModel).where(ConversationModel.id == conversation_id)
        if include_messages:
            stmt = stmt.options(selectinload(ConversationModel.messages))
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        if include_runs:
            latest = await self.session.scalar(
                select(AgentRunModel)
                .where(AgentRunModel.conversation_id == conversation_id)
                .order_by(AgentRunModel.created_at.desc(), AgentRunModel.id.desc())
                .limit(1)
            )
            # Seeded into __dict__ rather than assigned, so SQLAlchemy does not
            # treat this as a mutation of the relationship and try to flush it.
            model.__dict__["agent_runs"] = [latest] if latest is not None else []
        return model.to_entity()

    async def list_conversations(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        agent_selection: ConversationAgentSelection[UUID],
        status: ConversationStatus | None = None,
        conversation_type: ConversationType | None = None,
        metadata_filters: JsonObject | None = None,
        parent_id: UUID | None = None,
        archived: bool = False,
        cursor: UUID | None = None,
        limit: int = 20,
    ) -> tuple[list[ConversationEntity], UUID | None]:
        # One list or the other, never both: the archive is a place you go, not
        # a tail on the end of the history. Equality rather than "not archived"
        # so the same query serves both without a second code path.
        stmt = select(ConversationModel).where(
            ConversationModel.user_id == user_id,
            ConversationModel.pod_id == pod_id,
            ConversationModel.is_archived.is_(archived),
        )
        # Default: root conversations only. With parent_id: that conversation's
        # children (sub-agent conversations).
        if parent_id is None:
            stmt = stmt.where(ConversationModel.parent_id.is_(None))
        else:
            stmt = stmt.where(ConversationModel.parent_id == parent_id)
        if agent_selection.scope is not ConversationAgentScope.ALL:
            selected_agent_id = DEFAULT_POD_AGENT_ID
            if agent_selection.scope is ConversationAgentScope.NAMED:
                selected_agent_id = agent_selection.named_value
            agent_scope_id = func.coalesce(
                ConversationModel.agent_id, _DEFAULT_POD_AGENT_ID_SQL
            )
            stmt = stmt.where(agent_scope_id == selected_agent_id)
        if status is not None:
            stmt = stmt.where(
                ConversationModel.status.in_(_conversation_status_values_for_db(status))
            )
        if conversation_type is not None:
            stmt = stmt.where(
                ConversationModel.conversation_type == conversation_type.value
            )
        if metadata_filters:
            stmt = stmt.where(
                ConversationModel.conversation_metadata.op("@>")(metadata_filters)
            )
        return await self._list_conversations(stmt, cursor=cursor, limit=limit)

    async def _list_conversations(
        self,
        stmt,
        *,
        cursor: UUID | None,
        limit: int,
    ) -> tuple[list[ConversationEntity], UUID | None]:
        if cursor is not None:
            stmt = stmt.where(ConversationModel.id < cursor)
        stmt = stmt.order_by(ConversationModel.id.desc()).limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result.scalars())
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        next_cursor = rows[-1].id if has_more and rows else None
        return [row.to_entity() for row in rows], next_cursor

    async def lock_conversation(self, conversation_id: UUID) -> None:
        await self.session.execute(
            select(ConversationModel.id)
            .where(ConversationModel.id == conversation_id)
            .with_for_update()
        )

    async def list_children(
        self,
        *,
        parent_id: UUID,
        user_id: UUID,
        limit: int = 50,
        include_runs: bool = True,
    ) -> list[ConversationEntity]:
        """List child (sub-agent) conversations of a parent, newest first.

        Inverse of list_conversations (which hides children via parent_id IS NULL);
        reuses the ix_agent_conv_parent index. Scoped to the owning user.

        ``include_runs`` attaches each child's latest run only. The caller reads
        ``agent_runs[-1].status`` and nothing else, so eager-loading the full
        collection meant every run of every child to answer one question per
        child.
        """
        stmt = (
            select(ConversationModel)
            .where(
                ConversationModel.parent_id == parent_id,
                ConversationModel.user_id == user_id,
            )
            .order_by(
                ConversationModel.created_at.desc(),
                ConversationModel.id.desc(),
            )
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        models = list(result.scalars())
        if include_runs and models:
            latest_by_conversation = await self._latest_runs_for(
                [model.id for model in models]
            )
            for model in models:
                latest = latest_by_conversation.get(model.id)
                model.__dict__["agent_runs"] = [latest] if latest is not None else []
        return [model.to_entity() for model in models]

    async def create_agent_run(
        self,
        *,
        conversation_id: UUID,
        agent_id: UUID | None,
        agent_runtime: AgentRuntimeConfig,
        parent_run_id: UUID | None = None,
        metadata: JsonObject | None = None,
    ) -> AgentRunEntity:
        now = datetime.now(timezone.utc)
        model = AgentRunModel(
            conversation_id=conversation_id,
            agent_id=agent_id,
            parent_run_id=parent_run_id,
            status=AgentRunStatus.RUNNING.value,
            agent_runtime=agent_runtime.model_dump(mode="json"),
            started_at=now,
            run_metadata=metadata,
        )
        self.session.add(model)
        await self._update_conversation_status(
            conversation_id=conversation_id,
            status=ConversationStatus.RUNNING,
            output_data=None,
        )
        await self.session.flush()
        return model.to_entity()

    async def append_message(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID | None,
        draft: MessageDraft,
    ) -> MessageEntity:
        lock_result = await self.session.execute(
            select(ConversationModel)
            .where(ConversationModel.id == conversation_id)
            .with_for_update()
        )
        conversation = lock_result.scalar_one()
        # Archiving says "I am done with this"; a new message says otherwise,
        # whoever wrote it. Without this an archived conversation can still be
        # live -- a Slack thread is found by its origin and keeps receiving, a
        # run that was mid-flight when it was archived still answers -- and the
        # one place that would show you is the list it has been removed from.
        # The row is already locked FOR UPDATE here, and every writer in the
        # module goes through this method, so this is the one place it belongs.
        if conversation.is_archived:
            conversation.is_archived = False
        sequence_result = await self.session.execute(
            select(func.coalesce(func.max(MessageModel.sequence), -1)).where(
                MessageModel.conversation_id == conversation_id
            )
        )
        sequence = int(sequence_result.scalar_one()) + 1
        model = MessageModel(
            conversation_id=conversation.id,
            agent_run_id=agent_run_id,
            sequence=sequence,
            role=draft.role.value,
            kind=draft.kind.value,
            text=draft.text,
            tool_name=draft.tool_name,
            tool_call_id=draft.tool_call_id,
            tool_args=(
                to_json_value(draft.tool_args) if draft.tool_args is not None else None
            ),
            tool_result=(
                to_json_value(draft.tool_result)
                if draft.tool_result is not None
                else None
            ),
            message_metadata=draft.metadata,
        )
        self.session.add(model)
        await self.session.flush()
        return model.to_entity()

    async def list_messages(
        self,
        *,
        conversation_id: UUID,
        before_sequence: int | None = None,
        after_sequence: int | None = None,
        limit: int = 100,
    ) -> tuple[list[MessageEntity], int | None]:
        stmt = select(MessageModel).where(
            MessageModel.conversation_id == conversation_id
        )
        if before_sequence is not None:
            stmt = stmt.where(MessageModel.sequence < before_sequence)
        if after_sequence is not None:
            stmt = stmt.where(MessageModel.sequence > after_sequence)
        stmt = stmt.order_by(MessageModel.sequence.desc()).limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result.scalars())
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        next_cursor = rows[-1].sequence if has_more and rows else None
        return [row.to_entity() for row in reversed(rows)], next_cursor

    async def finish_agent_run(
        self,
        *,
        agent_run_id: UUID,
        status: AgentRunStatus,
        conversation_status: ConversationStatus | None = None,
        error: str | None = None,
        output_data: JsonValue | None = None,
    ) -> AgentRunFinishResult | None:
        result = await self.session.execute(
            select(AgentRunModel).where(AgentRunModel.id == agent_run_id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None

        current_status = AgentRunStatus(model.status)
        resolved_conversation_status = conversation_status or ConversationStatus(
            current_status.value
        )
        if current_status in TERMINAL_AGENT_RUN_STATUSES:
            # Nothing to end — but this used to report a `conversation_status`
            # it had only *inferred* from the run row and never written, so a
            # conversation out of step stayed that way. Nothing else recovers
            # one: the orphan sweep keys on `agent_runs.status`, so a terminal
            # run is invisible to it. This is the only place seeing both rows.
            repaired = await reconcile_conversation_to_terminal(
                self.session,
                conversation_id=model.conversation_id,
                status=resolved_conversation_status,
            )
            return AgentRunFinishResult(
                status=current_status,
                conversation_status=resolved_conversation_status,
                updated=False,
                conversation_repaired=repaired,
            )

        next_status = status
        if (
            current_status == AgentRunStatus.STOP_REQUESTED
            and status in TERMINAL_AGENT_RUN_STATUSES
        ):
            next_status = AgentRunStatus.STOPPED

        model.status = next_status.value
        model.error = error
        if output_data is not None:
            model.output_data = output_data
        resolved_conversation_status = conversation_status or ConversationStatus(
            next_status.value
        )
        await self._update_conversation_status(
            conversation_id=model.conversation_id,
            status=resolved_conversation_status,
            output_data=output_data,
        )
        if next_status in TERMINAL_AGENT_RUN_STATUSES:
            model.finished_at = datetime.now(timezone.utc)
        await self.session.flush()
        return AgentRunFinishResult(
            status=next_status,
            conversation_status=resolved_conversation_status,
            updated=True,
        )

    async def set_conversation_status(
        self,
        *,
        conversation_id: UUID,
        status: ConversationStatus,
    ) -> None:
        """Move a conversation's status with no run to finish.

        ``finish_agent_run`` is the usual path, but a suspended conversation has
        no active run — the run ended when the tool paused it — so cancelling its
        wait has to set the status directly.
        """
        await self._update_conversation_status(
            conversation_id=conversation_id,
            status=status,
        )

    async def _update_conversation_status(
        self,
        *,
        conversation_id: UUID,
        status: ConversationStatus,
        output_data: JsonValue | None = None,
    ) -> None:
        conversation = await self.session.get(ConversationModel, conversation_id)
        if conversation is None:
            return
        conversation.status = status.value
        if output_data is not None or status == ConversationStatus.RUNNING:
            conversation.output_data = output_data
