"""Conversations, their runs, and the messages that make them up."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import DateTime, func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.orm.attributes import set_committed_value

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
    AgentRunFinishResult,
    AgentRuntimeConfig,
    AgentRunStatus,
    ConversationAgentSelection,
    ConversationListCursor,
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
from app.modules.agent.infrastructure.conversation_run_queries import (
    ConversationRunQueriesMixin,
)


from app.modules.agent.infrastructure.repositories.conversation_activity import (
    is_activity,
    touch_activity as _touch_activity,
)
from app.modules.agent.infrastructure.repositories.conversation_list_page import (
    cursor_after,
    list_statement,
    page_by_activity,
)
from app.modules.agent.infrastructure.repositories.conversation_run_lifecycle import (
    finish_agent_run as _finish_agent_run,
    lock_conversation_row as _lock_conversation_row,
    update_conversation_status as _write_conversation_status,
)
from app.modules.agent.domain.subagent_spawn import SPAWN_KEY
from app.modules.agent.infrastructure.repositories.conversation_approval_queries import (
    ConversationApprovalQueriesMixin,
)
from app.modules.agent.infrastructure.repositories.conversation_opening_texts import (
    ConversationOpeningTextsMixin,
)


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
        model = ConversationModel(**ConversationModel.values_from(conversation))
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
        # Not `status` or `output_data`: those move with the run, in the run's
        # own transaction (`conversation_run_lifecycle`). An entity read before a
        # run ended and saved after -- a rename, an auto-title -- would write the
        # old RUNNING back over a finished conversation, and nothing would move
        # it again.
        model.conversation_metadata = conversation.metadata
        model.is_archived = conversation.is_archived
        await self.session.flush()
        return model.to_entity()

    async def get_conversation_metadata_key(
        self,
        conversation_id: UUID,
        key: str,
        *,
        for_update: bool = False,
    ) -> JsonValue | None:
        """Read a single key out of a conversation's metadata JSON blob."""
        statement = select(ConversationModel.conversation_metadata).where(
            ConversationModel.id == conversation_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
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

    async def remove_conversation_metadata_key(
        self, conversation_id: UUID, key: str, *, matching: JsonObject
    ) -> None:
        """Drop one metadata key, only while its object still holds ``matching``.

        Conditional, so a writer clearing the record it acted on cannot remove
        a newer one written under the same key meanwhile. Removed rather than
        set to null, so the key's presence -- what `list_metadata_due` finds
        rows by, through the GIN index -- means a record is there.
        """
        column = ConversationModel.conversation_metadata
        await self.session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == conversation_id)
            .where(column[key].op("@>")(literal(matching, JSONB)))
            .values(conversation_metadata=column - key)
        )
        await self.session.flush()

    async def list_metadata_due(
        self, *, key: str, due_field: str, due_before: datetime, limit: int
    ) -> list[tuple[UUID, JsonValue]]:
        """Conversations whose ``key`` record fell due before ``due_before``.

        ``due_field`` is an ISO timestamp inside the record. The key-exists
        test is what the metadata GIN index answers, so the scan is of the
        conversations holding such a record, not of every conversation.
        """
        column = ConversationModel.conversation_metadata
        record = column[key]
        result = await self.session.execute(
            select(ConversationModel.id, record)
            .where(column.has_key(key))
            .where(func.jsonb_typeof(record) == "object")
            .where(record[due_field].astext.cast(DateTime(timezone=True)) < due_before)
            .limit(limit)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def get_conversation(
        self,
        conversation_id: UUID,
        *,
        include_runs: bool = False,
    ) -> ConversationEntity | None:
        """Load a conversation, optionally with its latest run.

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
            # `set_committed_value`, not a plain assignment (which the ORM
            # reads as a mutation and tries to flush) and not a `__dict__`
            # write, which is what this used to do. A bare list in `__dict__`
            # is not an instrumented collection -- no `_sa_adapter` -- and
            # nothing notices until a later flush in the same unit of work asks
            # this relationship for its history, which then raises
            # `AttributeError` from SQLAlchemy's dependency processor,
            # attributed to whatever was being written at the time.
            #
            # No test here holds this: whether the flush consults the
            # relationship depends on what else is in it, and the case that
            # raised every time locally passed in CI. The argument is that an
            # uninstrumented collection is wrong whether or not today's flush
            # plan looks at it. `set_committed_value` is the documented way to
            # attach separately-queried rows as loaded state, and raises no
            # history events -- all the `__dict__` write was reaching for.
            set_committed_value(
                model, "agent_runs", [latest] if latest is not None else []
            )
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
        search: str | None = None,
        cursor: ConversationListCursor | None = None,
        limit: int = 20,
        in_channel: bool | None = None,
    ) -> tuple[list[ConversationEntity], ConversationListCursor | None]:
        stmt = list_statement(
            in_channel=in_channel,
            user_id=user_id,
            pod_id=pod_id,
            agent_selection=agent_selection,
            status=status,
            conversation_type=conversation_type,
            metadata_filters=metadata_filters,
            parent_id=parent_id,
            archived=archived,
            search=search,
        )
        return await page_by_activity(self.session, stmt, cursor=cursor, limit=limit)

    async def cursor_after(
        self, *, conversation_id: UUID, user_id: UUID, pod_id: UUID
    ) -> ConversationListCursor | None:
        return await cursor_after(
            self.session,
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
        )

    async def lock_conversation(self, conversation_id: UUID) -> None:
        await _lock_conversation_row(self.session, conversation_id=conversation_id)

    async def find_spawned_child(
        self, *, parent_id: UUID, parent_run_id: UUID, spawn_key: str
    ) -> ConversationEntity | None:
        """The child one parent run already spawned for this exact task.

        ``ix_agent_conv_parent`` narrows it to the parent's children, of which
        there are few; the metadata comparison is over those.
        """
        metadata = ConversationModel.conversation_metadata
        model = await self.session.scalar(
            select(ConversationModel)
            .where(
                ConversationModel.parent_id == parent_id,
                metadata["parent_run_id"].astext == str(parent_run_id),
                metadata[SPAWN_KEY].astext == spawn_key,
            )
            .order_by(ConversationModel.created_at)
            .limit(1)
        )
        return model.to_entity() if model is not None else None

    async def find_child_by_spawn_call(
        self, *, parent_id: UUID, spawn_call: str
    ) -> ConversationEntity | None:
        """The child the spawning call ``spawn_call`` created, if it did."""
        model = await self.session.scalar(
            select(ConversationModel).where(
                ConversationModel.parent_id == parent_id,
                ConversationModel.spawn_tool_call_id == spawn_call,
            )
        )
        return model.to_entity() if model is not None else None

    async def set_spawn_call(self, conversation_id: UUID, spawn_call: str) -> None:
        """Record the call that spawned ``conversation_id``; unique per parent."""
        await self.session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == conversation_id)
            .values(spawn_tool_call_id=spawn_call)
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
                # See `get_conversation` above for why this is not `__dict__`.
                set_committed_value(
                    model, "agent_runs", [latest] if latest is not None else []
                )
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
            touch_activity=True,
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
        # Same lock: a person's message or a notification moves the history
        # list. The agent's own messages do not (`conversation_activity`).
        if is_activity(draft):
            _touch_activity(conversation)
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
        error_code: str | None = None,
        error_reason: str | None = None,
        output_data: JsonValue | None = None,
        stamp: JsonObject | None = None,
    ) -> AgentRunFinishResult | None:
        """End a run once, whoever else is trying; see `conversation_run_lifecycle`."""
        return await _finish_agent_run(
            self.session,
            agent_run_id=agent_run_id,
            status=status,
            conversation_status=conversation_status,
            error=error,
            error_code=error_code,
            error_reason=error_reason,
            output_data=output_data,
            stamp=stamp,
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
            conversation_id=conversation_id, status=status
        )

    async def _update_conversation_status(
        self,
        *,
        conversation_id: UUID,
        status: ConversationStatus,
        output_data: JsonValue | None = None,
        touch_activity: bool = False,
    ) -> None:
        await _write_conversation_status(
            self.session,
            conversation_id=conversation_id,
            status=status,
            output_data=output_data,
            touch_activity=touch_activity,
        )
