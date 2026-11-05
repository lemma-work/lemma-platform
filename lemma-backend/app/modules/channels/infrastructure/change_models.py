"""Tables a channel's live subscribers are fed from: its change log and its
threads' counts.

A change a subscriber is shown -- a line written, edited, deleted, its
question or delivery moved, the channel's own fields or members -- is one row
appended to ``channel_changes`` under the channel's row lock, numbered by the
channel's counter (``channels.last_rev``). Nothing rewrites a line just to say
it moved. A thread's reply count is kept beside it (``channel_threads``), so a
reply writes neither its root nor a recount.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base


class ChannelChangeModel(Base):
    """One change of a channel, in the order its subscribers are sent them."""

    __tablename__ = "channel_changes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["channel_id", "pod_id"],
            ["channels.id", "channels.pod_id"],
            name="fk_channel_changes_channel",
            ondelete="CASCADE",
        ),
        # A hub's idle poll: the pod's channels that changed since its last.
        Index("ix_channel_changes_pod_at", "pod_id", "at"),
    )

    channel_id: Mapped[UUID] = mapped_column(primary_key=True)
    #: The channel's change number: gapless per channel until pruned.
    rev: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: The line it moved, or ``None`` for the channel's own fields or members.
    #: No foreign key: a line goes only with its channel, and the log with it.
    message_id: Mapped[UUID | None] = mapped_column(nullable=True)
    pod_id: Mapped[UUID] = mapped_column(nullable=False)
    #: When it was written, by the database's clock.
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )


class ChannelThreadModel(Base):
    """A line that has replies: how many, and the newest one's time."""

    __tablename__ = "channel_threads"
    __table_args__ = (
        # Of its root's channel, and follows the root when a fold moves it.
        ForeignKeyConstraint(
            ["root_id", "channel_id"],
            ["channel_messages.id", "channel_messages.channel_id"],
            name="fk_channel_threads_root",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
    )

    root_id: Mapped[UUID] = mapped_column(primary_key=True)
    channel_id: Mapped[UUID] = mapped_column(nullable=False)
    reply_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_reply_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


__all__ = ["ChannelChangeModel", "ChannelThreadModel"]
