"""SQLAlchemy model for conversation membership.

Its own module rather than a class in ``infrastructure/models.py`` because that
file is at the architecture ratchet's per-file limit; ``runtime_models`` is the
existing precedent for splitting a model out of it. Registered for migrations
in ``migrations/env.py`` alongside the others.

No ORM relationship back to ``ConversationModel``: adding one would push
that file past the same limit, and an implicit lazy load raises under async
SQLAlchemy anyway. Membership is read through
``conversation_participant_store`` instead, which asks for it explicitly.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDCreatedBase
from app.modules.agent.domain.participants import (
    ConversationParticipant as ConversationParticipantEntity,
    ConversationParticipantRole,
)


class ConversationParticipantModel(UUIDCreatedBase):
    """One person or one agent in a conversation."""

    __tablename__ = "agent_conversation_participants"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NULL) <> (agent_id IS NULL)",
            name="ck_conversation_participant_exactly_one_subject",
        ),
        # Postgres treats NULLs as distinct in a unique index, so the person
        # constraint does not bound how many agents a conversation may hold,
        # and vice versa. That is why these are two constraints and not one
        # over both columns.
        UniqueConstraint(
            "conversation_id", "user_id", name="uq_conversation_participant_user"
        ),
        UniqueConstraint(
            "conversation_id", "agent_id", name="uq_conversation_participant_agent"
        ),
        # "Which conversations am I in", which is how the list endpoint will
        # ask once it stops filtering on the owner column.
        Index("ix_conversation_participant_user", "user_id", "conversation_id"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    #: Set when the participant is a person; NULL when it is an agent.
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    #: Set when the participant is an agent; NULL when it is a person. CASCADE
    #: rather than the SET NULL that ``agent_conversations.agent_id`` uses: a
    #: conversation outlives its agent and falls back to the pod assistant, but
    #: a membership row for an agent that no longer exists means nothing.
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ConversationParticipantRole.MEMBER.value,
    )

    def to_entity(self) -> ConversationParticipantEntity:
        return ConversationParticipantEntity(
            id=self.id,
            created_at=self.created_at,
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            role=ConversationParticipantRole(self.role),
        )
