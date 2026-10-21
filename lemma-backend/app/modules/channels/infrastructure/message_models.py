"""Tables for what is said in a channel: its lines, who they name, the
questions they ask, and each send of a line outside Lemma.

A line is numbered per channel (``seq``). A question a line asks is its own
row, and every send to a place outside Lemma -- or push to a member -- is a
delivery row, unique per line and target.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class ChannelMessageModel(UUIDAuditBase):
    __tablename__ = "channel_messages"
    __table_args__ = (
        CheckConstraint(
            "sender_kind IN ('MEMBER', 'CONTACT', 'AGENT')",
            name="ck_channel_messages_sender_kind",
        ),
        # At most one: a member or an agent removed since leaves the line,
        # said by somebody no longer named (those ids are SET NULL).
        CheckConstraint(
            "num_nonnulls(sender_user_id, sender_contact_id, sender_agent_id) <= 1"
            " AND (sender_user_id IS NULL OR sender_kind = 'MEMBER')"
            " AND (sender_contact_id IS NULL OR sender_kind = 'CONTACT')"
            " AND (sender_agent_id IS NULL OR sender_kind = 'AGENT')",
            name="ck_channel_messages_one_sender",
        ),
        CheckConstraint(
            "num_nonnulls(on_behalf_user_id, on_behalf_contact_id) <= 1"
            " AND (sender_kind = 'AGENT'"
            " OR num_nonnulls(on_behalf_user_id, on_behalf_contact_id) = 0)",
            name="ck_channel_messages_only_an_agent_acts_for_someone",
        ),
        # Heard at a place, or carried to one from Lemma: never both.
        CheckConstraint(
            "num_nonnulls(source_id, target_source_id) <= 1",
            name="ck_channel_messages_heard_or_carried",
        ),
        ForeignKeyConstraint(
            ["channel_id", "pod_id"],
            ["channels.id", "channels.pod_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("channel_id", "seq", name="uq_channel_messages_seq"),
        # What a line's rows -- its question, thread, answers, deliveries --
        # hang from: the line *and its channel*, so the database refuses one
        # in another channel, and a fold moving the line carries them.
        UniqueConstraint("id", "channel_id", name="uq_channel_messages_id_channel"),
        # The same with the line's number, for its mentions: counted and
        # ordered from the mention's own index without visiting the line. The
        # number is the line's, cascaded, never a copy anybody keeps.
        UniqueConstraint(
            "id", "channel_id", "seq", name="uq_channel_messages_id_channel_seq"
        ),
        # Threads, replies and places of the line's own channel only
        # (``posting.LineMisplaced``). SET NULL of just the reference: the
        # line keeps its channel.
        ForeignKeyConstraint(
            ["thread_root_id", "channel_id"],
            ["channel_messages.id", "channel_messages.channel_id"],
            name="fk_channel_messages_thread_root",
            ondelete="SET NULL (thread_root_id)",
        ),
        ForeignKeyConstraint(
            ["reply_to_id", "channel_id"],
            ["channel_messages.id", "channel_messages.channel_id"],
            name="fk_channel_messages_reply_to",
            ondelete="SET NULL (reply_to_id)",
        ),
        # Deferrable: a fold moves a channel's places and its lines in two
        # statements, and checks them at its commit.
        ForeignKeyConstraint(
            ["source_id", "channel_id"],
            ["channel_sources.id", "channel_sources.channel_id"],
            name="fk_channel_messages_source",
            ondelete="SET NULL (source_id)",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        ForeignKeyConstraint(
            ["target_source_id", "channel_id"],
            ["channel_sources.id", "channel_sources.channel_id"],
            name="fk_channel_messages_target_source",
            ondelete="SET NULL (target_source_id)",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        Index(
            "uq_channel_messages_client",
            "channel_id",
            "client_message_id",
            unique=True,
            postgresql_where=text("client_message_id IS NOT NULL"),
        ),
        # A platform's message id, hashed to a uuid: an id of any length in
        # sixteen bytes (``external_key``).
        Index(
            "uq_channel_messages_external",
            "source_id",
            text("(md5(external_message_id)::uuid)"),
            unique=True,
            postgresql_where=text("external_message_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_thread",
            "thread_root_id",
            "seq",
            postgresql_where=text("thread_root_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_conversation",
            "conversation_id",
            "seq",
            postgresql_where=text("conversation_id IS NOT NULL"),
        ),
        # Search's windows, newest first (``line_search``): a pod's lines by
        # when they were said.
        Index("ix_channel_messages_pod_time", "pod_id", "created_at"),
        # Searched within a pod, or one of its channels: both beside the words
        # in the same GIN (``btree_gin``), so a channel's matches are found
        # without reading the pod's.
        Index(
            "ix_channel_messages_search",
            "pod_id",
            "channel_id",
            "search",
            postgresql_using="gin",
        ),
        Index(
            "ix_channel_messages_sender_user",
            "sender_user_id",
            postgresql_where=text("sender_user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_sender_contact",
            "sender_contact_id",
            postgresql_where=text("sender_contact_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_sender_agent",
            "sender_agent_id",
            postgresql_where=text("sender_agent_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_on_behalf_user",
            "on_behalf_user_id",
            postgresql_where=text("on_behalf_user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_on_behalf_contact",
            "on_behalf_contact_id",
            postgresql_where=text("on_behalf_contact_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_deleted_by",
            "deleted_by_user_id",
            postgresql_where=text("deleted_by_user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_run",
            "agent_run_id",
            postgresql_where=text("agent_run_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_reply_to",
            "reply_to_id",
            postgresql_where=text("reply_to_id IS NOT NULL"),
        ),
        # The latest line heard at a place (``conversation_at``, ``reply_address``).
        Index(
            "ix_channel_messages_source",
            "source_id",
            "seq",
            postgresql_where=text("source_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_target",
            "target_source_id",
            "seq",
            postgresql_where=text("target_source_id IS NOT NULL"),
        ),
    )

    channel_id: Mapped[UUID] = mapped_column(nullable=False)
    #: The channel's pod; a pod's delete reaches the line through its channel.
    pod_id: Mapped[UUID] = mapped_column(nullable=False)
    #: The line's place in its channel's timeline, gapless from 1.
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: What a client named the line it wrote, so a retry is the same line.
    #: A member's is kept in their own namespace (``domain.idempotency``).
    client_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Where it was heard: the place a person said it, on a platform.
    source_id: Mapped[UUID | None] = mapped_column(nullable=True)
    #: Where a line written in Lemma was carried: the place it went to.
    target_source_id: Mapped[UUID | None] = mapped_column(nullable=True)
    sender_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    sender_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Forgetting a contact empties their lines in place (``contact_channels``);
    #: SET NULL is what is left of the line's sender, so others' replies keep
    #: their thread and the timeline its numbers.
    sender_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    sender_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    on_behalf_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    on_behalf_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: Mentions, files and links beside the text (``domain.parts``).
    parts: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: Each word stemmed (``english``: "running" is "run") and as written
    #: (``simple``), so a word being typed is found by its start either way
    #: (``line_search``).
    search: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(body, ''))"
            " || to_tsvector('simple', coalesce(body, ''))",
            persisted=True,
        ),
        nullable=True,
    )
    members_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    external_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_thread_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: What answers a platform line in place: the adapter's own shape.
    reply_ref: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    reply_to_id: Mapped[UUID | None] = mapped_column(nullable=True)
    #: The line a thread hangs from, on every reply in it.
    thread_root_id: Mapped[UUID | None] = mapped_column(nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: An agent's answer made with a member's own access, shared by that
    #: member with everybody in the channel (``domain.projection``).
    shared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The member who deleted it: its writer, or somebody who may change the
    #: channel removing it. Absent when its platform said it was deleted.
    deleted_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ChannelMentionModel(UUIDAuditBase):
    """Somebody a line names: a member, a contact or an agent."""

    __tablename__ = "channel_mentions"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(user_id, contact_id, agent_id) = 1",
            name="ck_channel_mentions_one_named",
        ),
        # The line's channel and number, cascaded: a fold moving and
        # renumbering the line moves the mention, and a line's delete takes it.
        ForeignKeyConstraint(
            ["message_id", "channel_id", "seq"],
            [
                "channel_messages.id",
                "channel_messages.channel_id",
                "channel_messages.seq",
            ],
            name="fk_channel_mentions_line",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        Index(
            "uq_channel_mentions_named",
            "message_id",
            text("coalesce(user_id, contact_id, agent_id)"),
            unique=True,
        ),
        # A member's unread mentions, and only those: reading one takes it
        # out, so counting them never visits what was read.
        Index(
            "ix_channel_mentions_user",
            "user_id",
            "pod_id",
            "channel_id",
            "seq",
            postgresql_where=text("user_id IS NOT NULL AND read_at IS NULL"),
        ),
        Index(
            "ix_channel_mentions_contact",
            "contact_id",
            postgresql_where=text("contact_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_mentions_agent",
            "agent_id",
            postgresql_where=text("agent_id IS NOT NULL"),
        ),
    )

    message_id: Mapped[UUID] = mapped_column(nullable=False)
    channel_id: Mapped[UUID] = mapped_column(nullable=False)
    #: The line's pod, for a member's mentions across it; never changes.
    pod_id: Mapped[UUID] = mapped_column(nullable=False)
    #: The line's ``seq``, held to it by ``fk_channel_mentions_line``.
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
    )
    #: When the member named read it where it was said: a top-level line's
    #: by reading the timeline past it, a reply's by reading its thread
    #: (``members.mark_read``). Unread while ``NULL``.
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
