"""Conversations, the runs that answer them, and the messages in both.

These three are mutually referential -- a run names its conversation as a class,
and a conversation orders its messages and runs through lambdas closing over
theirs -- so they share a module rather than importing each other in a cycle."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import UUIDAuditBase, UUIDCreatedBase
from app.modules.agent.domain.entities import (
    AgentRun as AgentRunEntity,
    Conversation as ConversationEntity,
    Message as MessageEntity,
)
from app.modules.agent.domain.conversation_lifecycle import conversation_status_for
from app.modules.agent.domain.run_state import RunFailure
from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.run_state import RunState
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    AgentRunStatus,
    AnswerKind,
    ConversationOrigin,
    ConversationStatus,
    ConversationType,
    MessageKind,
    QueueState,
    RunTrigger,
    WrittenIn,
)
from app.modules.agent.infrastructure.model_converters import (
    agent_runtime_from_json,
    default_agent_runtime,
)

from app.modules.agent.infrastructure.models.agent import AgentModel
from app.modules.agent.infrastructure.models.typed_state import (
    conversation_state_fields,
    conversation_state_values,
    enum_column,
    flag_column,
    json_column,
    json_list_column,
    message_state_of,
    message_state_values,
    run_state_of,
    run_state_values,
    sequence_column,
    text_column,
    timestamp_column,
)


class ConversationModel(UUIDAuditBase):
    """Conversation shared by the pod assistant and pod agents."""

    __tablename__ = "agent_conversations"
    __table_args__ = (
        # A channel of the conversation's own pod. Its channel going leaves the
        # conversation in no channel -- never in no pod.
        ForeignKeyConstraint(
            ["channel_id", "pod_id"],
            ["channels.id", "channels.pod_id"],
            name="fk_agent_conversations_channel",
            ondelete="SET NULL (channel_id)",
        ),
        # The history list's order, `(last_activity_at, id)` descending. The
        # agent-scoped one is on the same COALESCE the listing spells out.
        Index(
            "ix_agent_conv_user_pod_roots_activity",
            "user_id",
            "pod_id",
            "last_activity_at",
            "id",
            postgresql_where=text("parent_id IS NULL"),
        ),
        Index(
            "ix_agent_conv_user_pod_agent_roots_activity",
            "user_id",
            "pod_id",
            text("COALESCE(agent_id, pod_id)"),
            "last_activity_at",
            "id",
            postgresql_where=text("parent_id IS NULL"),
        ),
        Index(
            "ix_agent_conv_metadata",
            "conversation_metadata",
            postgresql_using="gin",
        ),
        # A person has one live conversation per agent in a channel.
        Index(
            "uq_agent_conversations_channel_person",
            "channel_id",
            text("COALESCE(agent_id, pod_id)"),
            text("COALESCE(contact_id, user_id)"),
            unique=True,
            postgresql_where=text(
                "channel_id IS NOT NULL AND parent_id IS NULL AND NOT is_archived"
            ),
        ),
        Index(
            "uq_agent_conversation_origin",
            "origin_type",
            "origin_id",
            unique=True,
            postgresql_where=text("origin_id IS NOT NULL"),
        ),
        # One child per spawning call: a resumed turn re-runs an interrupted
        # `spawn_subagent` under its recorded call id and gets the same child.
        Index(
            "uq_agent_conv_spawn_call",
            "parent_id",
            "spawn_tool_call_id",
            unique=True,
            postgresql_where=text("spawn_tool_call_id IS NOT NULL"),
        ),
        # A parent's children by task (`find_spawned_child`). Not unique: the
        # same task spawned from two runs is two children, and the run a child
        # answers to moves on every `send`.
        Index(
            "ix_agent_conv_spawn_key",
            "parent_id",
            "spawn_key",
            postgresql_where=text("spawn_key IS NOT NULL"),
        ),
        # The containment matches `PodAskQueries` makes on every finished run.
        Index(
            "ix_agent_conv_ask",
            "ask",
            postgresql_using="gin",
            postgresql_where=text("ask IS NOT NULL"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    #: The contact this conversation answers, when it answers one. Forgetting
    #: the contact deletes what was said with them.
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    #: The channel this conversation's agent works in, when it lives on one.
    #: A conversation started in Lemma, and an agent's own task, have none.
    channel_id: Mapped[UUID | None] = mapped_column(index=True, nullable=True)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_runtime: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    origin_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    origin_id: Mapped[UUID | None] = mapped_column(nullable=True)
    conversation_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ConversationType.CHAT.value,
        index=True,
    )
    status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    output_data: Mapped[dict | str | None] = mapped_column(JSONB, nullable=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    conversation_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: ``<parent run id>:<tool call id>`` of the ``spawn_subagent`` call that
    #: created this child; unique per parent (``uq_agent_conv_spawn_call``).
    spawn_tool_call_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # The typed state (`Conversation` documents each field; `typed_state`).
    origin: Mapped[ConversationOrigin] = enum_column(
        ConversationOrigin, length=32, default=ConversationOrigin.CHAT
    )
    workflow_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    parent_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    spawn_key: Mapped[str | None] = text_column()
    reports_to_parent: Mapped[bool] = flag_column()
    delegations_out: Mapped[bool] = flag_column()
    ask: Mapped[dict[str, object] | None] = json_column()
    call: Mapped[dict[str, object] | None] = json_column()
    cwd: Mapped[str | None] = text_column()
    workspace_id: Mapped[str | None] = text_column()
    workspace_name: Mapped[str | None] = text_column()
    repo: Mapped[dict[str, object] | None] = json_column()
    todos: Mapped[list[object]] = json_list_column()
    #: Put away, not deleted: the row stays, the listing skips it, and a new
    #: message clears it (`append_message`).
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: When something outside the agent last happened here -- a person wrote,
    #: a notification landed, a run started or finished; the history list's
    #: order. The rule is `db/conversation_activity`. Not
    #: `updated_at`, which a rename or a status repair also moves.
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )

    owner: Mapped[Any] = relationship("User", foreign_keys=[user_id])
    pod: Mapped[Any] = relationship("Pod", foreign_keys=[pod_id])
    organization: Mapped[Any] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    agent: Mapped["AgentModel | None"] = relationship(
        "AgentModel",
        foreign_keys=[agent_id],
    )
    messages: Mapped[list["MessageModel"]] = relationship(
        "MessageModel",
        back_populates="conversation",
        cascade="all, delete-orphan",
        # The FK already declares ON DELETE CASCADE, so the database removes
        # these rows itself. Without passive_deletes SQLAlchemy insists on
        # loading every child into the session first and deleting them one
        # at a time -- which on a large collection is a memory event, not a
        # slow query.
        passive_deletes=True,
        order_by=lambda: MessageModel.sequence,
        foreign_keys=lambda: [MessageModel.conversation_id],
    )
    agent_runs: Mapped[list["AgentRunModel"]] = relationship(
        "AgentRunModel",
        back_populates="conversation",
        cascade="all, delete-orphan",
        # The FK already declares ON DELETE CASCADE, so the database removes
        # these rows itself. Without passive_deletes SQLAlchemy insists on
        # loading every child into the session first and deleting them one
        # at a time -- which on a large collection is a memory event, not a
        # slow query.
        passive_deletes=True,
        order_by=lambda: AgentRunModel.created_at,
        foreign_keys=lambda: [AgentRunModel.conversation_id],
    )

    def __str__(self) -> str:
        return self.title or f"conversation {str(self.id)[:8]}"

    @staticmethod
    def values_from(conversation: ConversationEntity) -> dict[str, object]:
        """The row a new conversation entity is inserted as.

        One mapping for every insert path -- the ORM add, and the upsert a
        caller-reserved id goes through -- so a column added to one cannot be
        forgotten in the other.
        """
        return {
            "id": conversation.id,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
            "user_id": conversation.user_id,
            "pod_id": conversation.pod_id,
            "contact_id": conversation.contact_id,
            "channel_id": conversation.channel_id,
            "organization_id": conversation.organization_id,
            "agent_id": conversation.agent_id,
            "title": conversation.title,
            "instructions": conversation.instructions,
            "agent_runtime": (
                conversation.agent_runtime.model_dump(mode="json")
                if conversation.agent_runtime
                else None
            ),
            "origin_type": conversation.origin_type,
            "origin_id": conversation.origin_id,
            "conversation_type": conversation.type.value,
            "status": conversation.status.value if conversation.status else None,
            "output_data": conversation.output,
            "parent_id": conversation.parent_id,
            "conversation_metadata": conversation.metadata,
            "is_archived": conversation.is_archived,
            **conversation_state_values(conversation),
        }

    def to_entity(self) -> ConversationEntity:
        loaded_messages = self.__dict__.get("messages", [])
        loaded_runs = self.__dict__.get("agent_runs", [])
        latest_run = loaded_runs[-1] if loaded_runs else None
        return ConversationEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            user_id=self.user_id,
            pod_id=self.pod_id,
            contact_id=self.contact_id,
            channel_id=self.channel_id,
            organization_id=self.organization_id,
            agent_id=self.agent_id,
            title=self.title,
            instructions=self.instructions,
            agent_runtime=agent_runtime_from_json(self.agent_runtime),
            origin_type=self.origin_type,
            origin_id=self.origin_id,
            parent_id=self.parent_id,
            type=ConversationType(
                self.conversation_type or ConversationType.CHAT.value
            ),
            status=ConversationStatus(self.status)
            if self.status
            else conversation_status_for(AgentRunStatus(latest_run.status))
            if latest_run
            else None,
            output=self.output_data,
            metadata=self.conversation_metadata,
            **conversation_state_fields(self),
            is_archived=bool(self.is_archived),  # None until the INSERT.
            last_activity_at=self.last_activity_at,
            last_run_status=latest_run.status if latest_run else None,
            last_run_error=latest_run.error if latest_run else None,
            last_run_error_code=_failure_code(latest_run),
            last_run_error_reason=_failure_reason(latest_run),
            last_run_finished_at=latest_run.finished_at if latest_run else None,
            messages=[message.to_entity() for message in loaded_messages],
            agent_runs=[agent_run.to_entity() for agent_run in loaded_runs],
        )


class AgentRunModel(UUIDAuditBase):
    """Internal execution attempt for a conversation."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_run_conversation_created", "conversation_id", "created_at"),
        Index("ix_agent_run_conversation_status", "conversation_id", "status"),
        Index(
            "uq_agent_active_run_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text(
                "status IN ('RUNNING', 'STOP_REQUESTED', 'running', 'stop_requested')"
            ),
        ),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    parent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AgentRunStatus.RUNNING.value,
        index=True,
    )
    agent_runtime: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=default_agent_runtime,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_data: Mapped[dict | str | None] = mapped_column(JSONB, nullable=True)
    run_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # The typed state (`RunState` documents each field; `typed_state`).
    trigger: Mapped[RunTrigger] = enum_column(
        RunTrigger, length=32, default=RunTrigger.MESSAGE
    )
    private_note: Mapped[bool] = flag_column()
    asked_in_lemma: Mapped[bool] = flag_column()
    written_in: Mapped[WrittenIn | None] = enum_column(WrittenIn, length=16)
    resumed_tool_call_id: Mapped[str | None] = text_column()
    retried_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    queued_behind_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    queued_leftovers_behind: Mapped[list[object]] = json_list_column()
    stop_requested_after_sequence: Mapped[int | None] = sequence_column()
    delivered_to_parent_at: Mapped[datetime | None] = timestamp_column()
    suspended_while_parked: Mapped[dict[str, object] | None] = json_column()
    schedule_firing: Mapped[dict[str, object] | None] = json_column()
    failure: Mapped[dict[str, object] | None] = json_column()

    conversation: Mapped["ConversationModel"] = relationship(
        ConversationModel,
        back_populates="agent_runs",
        foreign_keys=[conversation_id],
    )
    agent: Mapped["AgentModel | None"] = relationship(
        AgentModel,
        foreign_keys=[agent_id],
    )
    parent_run: Mapped["AgentRunModel | None"] = relationship(
        "AgentRunModel",
        remote_side="AgentRunModel.id",
        foreign_keys=[parent_run_id],
    )
    messages: Mapped[list["MessageModel"]] = relationship(
        "MessageModel",
        back_populates="agent_run",
        order_by=lambda: MessageModel.sequence,
        foreign_keys=lambda: [MessageModel.agent_run_id],
    )

    def state(self) -> RunState:
        return run_state_of(self)

    #: The typed-state columns of a run in a ``RunState``, as written.
    state_values = staticmethod(run_state_values)

    def to_entity(self) -> AgentRunEntity:
        return AgentRunEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            conversation_id=self.conversation_id,
            agent_id=self.agent_id,
            parent_run_id=self.parent_run_id,
            status=AgentRunStatus(self.status),
            agent_runtime=AgentRuntimeConfig.model_validate(self.agent_runtime),
            started_at=self.started_at,
            finished_at=self.finished_at,
            error=self.error,
            output_data=self.output_data,
            metadata=self.run_metadata,
            state=run_state_of(self),
            messages=[
                message.to_entity() for message in self.__dict__.get("messages", [])
            ],
        )


class MessageModel(UUIDCreatedBase):
    """Append-only conversation message."""

    __tablename__ = "agent_messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_agent_message_sequence"
        ),
        Index("ix_agent_message_conversation_sequence", "conversation_id", "sequence"),
        Index("ix_agent_message_run_sequence", "agent_run_id", "sequence"),
        Index("ix_agent_message_tool_call", "tool_call_id"),
        # One message per send (`find_user_message_by_client_id`).
        Index(
            "uq_agent_message_client_id",
            "conversation_id",
            "client_message_id",
            unique=True,
            postgresql_where=text("client_message_id IS NOT NULL"),
        ),
        # What is queued in a conversation, without reading its transcript.
        Index(
            "ix_agent_message_queued",
            "conversation_id",
            "queue_state",
            postgresql_where=text("queue_state <> 'NONE'"),
        ),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=MessageKind.TEXT.value,
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_args: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB, nullable=True
    )
    tool_result: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB, nullable=True
    )
    tool_call_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # The typed state (`MessageState` documents each field; `typed_state`).
    queue_state: Mapped[QueueState] = enum_column(
        QueueState, length=32, default=QueueState.NONE
    )
    steered_into_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    steer_dispatched_at: Mapped[datetime | None] = timestamp_column()
    client_message_id: Mapped[str | None] = text_column()
    private_note: Mapped[bool] = flag_column()
    private_note_in_dm: Mapped[bool] = flag_column()
    written_in: Mapped[WrittenIn | None] = enum_column(WrittenIn, length=16)
    answer: Mapped[AnswerKind | None] = enum_column(AnswerKind, length=16)
    held_for_a_person: Mapped[bool] = flag_column()
    folded_from: Mapped[UUID | None] = mapped_column(nullable=True)
    synthetic_tool_return: Mapped[bool] = flag_column()
    parked_in_run_id: Mapped[UUID | None] = mapped_column(nullable=True)

    conversation: Mapped["ConversationModel"] = relationship(
        ConversationModel,
        back_populates="messages",
        foreign_keys=[conversation_id],
    )
    agent_run: Mapped["AgentRunModel | None"] = relationship(
        AgentRunModel,
        back_populates="messages",
        foreign_keys=[agent_run_id],
    )

    def state(self) -> MessageState:
        return message_state_of(self)

    #: The typed-state columns of a message in a ``MessageState``, as written.
    state_values = staticmethod(message_state_values)

    def to_entity(self) -> MessageEntity:
        return MessageEntity(
            id=self.id,
            created_at=self.created_at,
            conversation_id=self.conversation_id,
            sequence=self.sequence,
            agent_run_id=self.agent_run_id,
            role=self.role,
            kind=MessageKind(self.kind),
            text=self.text,
            tool_name=self.tool_name,
            tool_call_id=self.tool_call_id,
            tool_args=self.tool_args,
            tool_result=self.tool_result,
            metadata=self.message_metadata,
            state=message_state_of(self),
        )


def _failure(run: AgentRunModel | None) -> RunFailure | None:
    if run is None or run.status != AgentRunStatus.FAILED.value:
        return None
    return RunFailure.from_json(run.failure)


def _failure_code(run: AgentRunModel | None) -> str | None:
    failure = _failure(run)
    return failure.code if failure else None


def _failure_reason(run: AgentRunModel | None) -> str | None:
    failure = _failure(run)
    return failure.reason if failure else None
