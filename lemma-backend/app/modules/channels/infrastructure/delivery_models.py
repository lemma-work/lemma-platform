"""Tables for sending a channel's lines outside Lemma.

``channel_message_deliveries`` is every send of a line to a place, or push to
a member, unique per line and target; ``channel_delivery_attempts`` each try
of one, so a send that met a 429 and then went shows both. A member's pushes
are coalesced first: ``channel_push_pending`` holds, per member and channel,
the lines waiting to be pushed, and one delivery goes per window.

A delivery hangs from its line by ``(message_id, channel_id)``, so a line that
moves channel (a contact's channel folded into another) takes its deliveries
with it, and is deleted with it. There is no foreign key to the channel
itself: the line's already says whose it is, and a second one would be one
more referencing-table lookup per channel deleted.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base, UUIDAuditBase
from app.modules.channels.domain.entities import DeliveryReasonCode

_DUE = "status IN ('PENDING', 'FAILED')"
_REASON_CODES = ", ".join(f"'{code.value}'" for code in DeliveryReasonCode)
#: How a platform's message id is indexed: its md5 as a uuid, 16 bytes,
#: rather than the id itself (unbounded) or its md5 as text (33).
EXTERNAL_KEY = "(md5(external_message_id)::uuid)"


class ChannelMessageDeliveryModel(UUIDAuditBase):
    """One send of a line outside Lemma: to a place, or to a member's reach.

    The only record of whether it went: unique per line and target, so a
    retried job finds the row and never sends twice.
    """

    __tablename__ = "channel_message_deliveries"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(source_id, recipient_user_id) = 1",
            name="ck_channel_message_deliveries_one_target",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'SENT', 'FAILED', 'SKIPPED', 'DEAD')",
            name="ck_channel_message_deliveries_status",
        ),
        CheckConstraint(
            f"reason_code IS NULL OR reason_code IN ({_REASON_CODES})",
            name="ck_channel_message_deliveries_reason_code",
        ),
        CheckConstraint(
            "(status IN ('PENDING', 'SENT')) = (reason_code IS NULL)",
            name="ck_channel_message_deliveries_reason_when_not_sent",
        ),
        CheckConstraint(
            "recipient_user_id IS NOT NULL OR (from_seq IS NULL AND lines = 1 "
            "AND NOT urgent)",
            name="ck_channel_message_deliveries_window_is_a_push",
        ),
        ForeignKeyConstraint(
            ["message_id", "channel_id"],
            ["channel_messages.id", "channel_messages.channel_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        Index(
            "uq_channel_message_deliveries_target",
            "message_id",
            text("coalesce(source_id, recipient_user_id)"),
            unique=True,
        ),
        Index(
            "ix_channel_message_deliveries_due",
            "next_attempt_at",
            postgresql_where=text(_DUE),
        ),
        # What a target has yet to send, in the order its lines were written:
        # one probe answers "is an older one still to go" (``claim``).
        Index(
            "ix_cmd_due_source",
            "source_id",
            "channel_id",
            "seq",
            postgresql_where=text(f"{_DUE} AND source_id IS NOT NULL"),
        ),
        Index(
            "ix_cmd_due_recipient",
            "recipient_user_id",
            "channel_id",
            "seq",
            postgresql_where=text(f"{_DUE} AND recipient_user_id IS NOT NULL"),
        ),
        # A status report names a platform's message id; a reply in a thread
        # names the one a line was sent as, at its place. Both also serve the
        # cascades from a source and a user.
        Index(
            "ix_channel_message_deliveries_external",
            text(EXTERNAL_KEY),
            postgresql_where=text("external_message_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_message_deliveries_source",
            "source_id",
            text(EXTERNAL_KEY),
            postgresql_where=text("source_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_message_deliveries_recipient",
            "recipient_user_id",
            postgresql_where=text("recipient_user_id IS NOT NULL"),
        ),
        # Settled pushes, oldest first (ids are time-ordered): what the
        # retention sweep deletes.
        Index(
            "ix_channel_message_deliveries_settled_pushes",
            "id",
            postgresql_where=text(
                "recipient_user_id IS NOT NULL "
                "AND status IN ('SENT', 'SKIPPED', 'DEAD')"
            ),
        ),
    )

    message_id: Mapped[UUID] = mapped_column(nullable=False)
    channel_id: Mapped[UUID] = mapped_column(nullable=False)
    pod_id: Mapped[UUID] = mapped_column(nullable=False)
    #: The line's ``seq``: a target's sends go in this order.
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: A place the line goes to (a contact's chat, a group) ...
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("channel_sources.id", ondelete="CASCADE"), nullable=True
    )
    #: ... or a member it is pushed to, wherever they are reached.
    recipient_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    #: The platform it went out on; set when sent.
    platform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="PENDING", server_default=text("'PENDING'")
    )
    #: Why it was skipped, failed or given up (``DeliveryReasonCode``) ...
    reason_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: ... and the same in words a member reads.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: A push: the first line it stands for (``seq`` is the one it leads
    #: with), how many lines the member hears in that range, and whether one
    #: of them names or asks them.
    from_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lines: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    urgent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    external_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set while a sender holds it, cleared with the outcome: a claim that
    #: lapsed with this still set is a send nobody finished.
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ChannelDeliveryAttemptModel(Base):
    """One try of a delivery: when it began and ended, and what it ended as.

    Written as the sender claims it (``n`` is the delivery's ``attempts``) and
    finished with the outcome, so a lapsed claim is a try with no end.
    """

    __tablename__ = "channel_delivery_attempts"
    __table_args__ = (
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('SENT', 'SKIPPED', 'FAILED', 'DEAD')",
            name="ck_channel_delivery_attempts_outcome",
        ),
    )

    delivery_id: Mapped[UUID] = mapped_column(
        ForeignKey("channel_message_deliveries.id", ondelete="CASCADE"),
        primary_key=True,
    )
    n: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outcome: Mapped[str | None] = mapped_column(String(10), nullable=True)
    #: What the platform last answered, when it said: an HTTP status.
    provider_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: ``DeliveryReasonCode`` of an outcome other than ``SENT``.
    error_code: Mapped[str | None] = mapped_column(String(20), nullable=True)


class ChannelPushPendingModel(Base):
    """The lines of one channel waiting to be pushed to one member.

    One row per member and channel. A line they hear is folded in
    (``from_seq`` .. ``to_seq``, ``lines`` of them, led by ``lead_seq`` -- the
    newest that names or asks them, else the newest); ``next_attempt_at`` is
    when they go as one push. Once pushed the row stays empty, with
    ``pushed_at``, until the window after it has passed: a line in that
    window waits for its end rather than going alone.
    """

    __tablename__ = "channel_push_pending"
    __table_args__ = (
        CheckConstraint(
            "(lines = 0) = (from_seq IS NULL) AND (from_seq IS NULL) = "
            "(to_seq IS NULL) AND (to_seq IS NULL) = (lead_seq IS NULL) AND "
            "(lead_seq IS NULL) = (next_attempt_at IS NULL)",
            name="ck_channel_push_pending_window",
        ),
        ForeignKeyConstraint(
            ["channel_id", "pod_id"],
            ["channels.id", "channels.pod_id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_channel_push_pending_due",
            "next_attempt_at",
            postgresql_where=text("next_attempt_at IS NOT NULL"),
        ),
        Index("ix_channel_push_pending_recipient", "recipient_user_id"),
    )

    channel_id: Mapped[UUID] = mapped_column(primary_key=True)
    recipient_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    pod_id: Mapped[UUID] = mapped_column(nullable=False)
    from_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    to_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lead_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lines: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    urgent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pushed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


__all__ = [
    "EXTERNAL_KEY",
    "ChannelDeliveryAttemptModel",
    "ChannelMessageDeliveryModel",
    "ChannelPushPendingModel",
]
