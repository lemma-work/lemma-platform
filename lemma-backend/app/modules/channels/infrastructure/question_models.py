"""The table of questions lines put to one person.

A question is its line's: the composite foreign key to ``(id, channel_id)``
of ``channel_messages`` keeps its channel the line's -- a fold moving the line
moves it (ON UPDATE CASCADE) -- and it goes with the line. What asked it is
typed (``asker``), and names the conversation or workflow run that asked, so
the asker's own lifecycle reaches it: a conversation or run deleted leaves its
questions with nobody to ask for, and the sweeper cancels them
(``QuestionRepository.cancel_orphaned``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base

_WAITING = "state IN ('OPEN', 'ANSWERING')"


class ChannelQuestionModel(Base):
    """A question a line puts to one person, and what became of it."""

    __tablename__ = "channel_questions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('ASK', 'APPROVAL', 'FORM')", name="ck_channel_questions_kind"
        ),
        CheckConstraint(
            "state IN ('OPEN', 'ANSWERING', 'ANSWERED', 'CANCELLED', 'EXPIRED')",
            name="ck_channel_questions_state",
        ),
        CheckConstraint(
            "num_nonnulls(awaits_user_id, awaits_contact_id) = 1",
            name="ck_channel_questions_awaits_one",
        ),
        CheckConstraint(
            "asker IN ('TOOL', 'WORKFLOW', 'MESSAGE')",
            name="ck_channel_questions_asker",
        ),
        # Which of the asker's ids it may name. Not that it names one: each is
        # SET NULL when its conversation or run is deleted, which is how the
        # sweeper finds what was orphaned.
        CheckConstraint(
            "(asker_conversation_id IS NULL OR asker IN ('TOOL', 'MESSAGE'))"
            " AND (asker_workflow_run_id IS NULL OR asker = 'WORKFLOW')",
            name="ck_channel_questions_asker_ids",
        ),
        CheckConstraint(
            "awaits_policy IN ('ONE')", name="ck_channel_questions_awaits_policy"
        ),
        ForeignKeyConstraint(
            ["message_id", "channel_id"],
            ["channel_messages.id", "channel_messages.channel_id"],
            name="fk_channel_questions_line",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        # What the asker names it by, once per pod: a retried ask is the same
        # question.
        UniqueConstraint("pod_id", "ref", name="uq_channel_questions_ref"),
        Index(
            "ix_channel_questions_waiting_member",
            "awaits_user_id",
            "pod_id",
            postgresql_where=text(f"{_WAITING} AND awaits_user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_questions_waiting_contact",
            "awaits_contact_id",
            postgresql_where=text(f"{_WAITING} AND awaits_contact_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_questions_expiry",
            "expires_at",
            postgresql_where=text(f"{_WAITING} AND expires_at IS NOT NULL"),
        ),
        # A claim the sweeper may have to put back: few, and short-lived.
        Index(
            "ix_channel_questions_claimed",
            "claimed_at",
            postgresql_where=text("state = 'ANSWERING'"),
        ),
        # Still waiting with nobody left to ask for (``cancel_orphaned``).
        Index(
            "ix_channel_questions_orphaned",
            "created_at",
            postgresql_where=text(
                f"{_WAITING} AND asker_conversation_id IS NULL"
                " AND asker_workflow_run_id IS NULL"
            ),
        ),
        Index(
            "ix_channel_questions_batch",
            "pod_id",
            "batch_id",
            postgresql_where=text("batch_id IS NOT NULL"),
        ),
        # What a conversation or run asked, newest first; and the SET NULL
        # each one's delete makes.
        Index(
            "ix_channel_questions_asker_conversation",
            "asker_conversation_id",
            "created_at",
            postgresql_where=text("asker_conversation_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_questions_asker_run",
            "asker_workflow_run_id",
            postgresql_where=text("asker_workflow_run_id IS NOT NULL"),
        ),
        # The rest only serve a person's delete: a closed question keeps who
        # it waited on and who answered it.
        Index(
            "ix_channel_questions_awaits_user",
            "awaits_user_id",
            postgresql_where=text("awaits_user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_questions_awaits_contact",
            "awaits_contact_id",
            postgresql_where=text("awaits_contact_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_questions_answered_user",
            "answered_by_user_id",
            postgresql_where=text("answered_by_user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_questions_answered_contact",
            "answered_by_contact_id",
            postgresql_where=text("answered_by_contact_id IS NOT NULL"),
        ),
    )

    message_id: Mapped[UUID] = mapped_column(primary_key=True)
    #: The line's channel, held to it by ``fk_channel_questions_line``.
    channel_id: Mapped[UUID] = mapped_column(nullable=False)
    #: The line's pod; a pod's delete reaches the question through its line.
    pod_id: Mapped[UUID] = mapped_column(nullable=False)
    #: The asker's idempotency key for it, unique per pod. Never parsed: what
    #: asked it is ``asker`` and its ids.
    ref: Mapped[str] = mapped_column(Text, nullable=False)
    #: ``TOOL``: an agent's paused ``ask_user`` or approval, in
    #: ``asker_conversation_id``. ``WORKFLOW``: a workflow's form, in
    #: ``asker_workflow_run_id``. ``MESSAGE``: a question one member's agent
    #: put to another, from ``asker_conversation_id``.
    asker: Mapped[str] = mapped_column(String(10), nullable=False)
    asker_conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True
    )
    asker_workflow_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workflow_flow_runs.id", ondelete="SET NULL"), nullable=True
    )
    #: What within the asker it is: a tool call, a form's node and wait, a key.
    asker_key: Mapped[str] = mapped_column(Text, nullable=False)
    #: Who may answer: ``ONE`` -- the one person it waits on.
    awaits_policy: Mapped[str] = mapped_column(
        String(10), nullable=False, default="ONE", server_default=text("'ONE'")
    )
    #: Questions sent together; the asker is woken when the last one closes.
    batch_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    state: Mapped[str] = mapped_column(
        String(10), nullable=False, default="OPEN", server_default=text("'OPEN'")
    )
    #: The question as asked: choices, a form's schema, an approval's tool.
    question: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    awaits_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    awaits_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    #: The answer that took effect -- the asker's, not the request's.
    answer: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    answered_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    answered_by_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set while an answer is being taken; a claim older than a minute is
    #: abandoned and may be taken again.
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
