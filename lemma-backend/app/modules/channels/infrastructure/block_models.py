"""Who a member has blocked, in one pod's chats.

A block is the reader's alone: it changes what they are shown and told, and
nothing anybody else reads, nor what the pod's agents do. It names a member
of the pod or one of its contacts, exactly one, and goes when the pod, the
member who blocked, or the person blocked does.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDCreatedBase


class PersonBlockModel(UUIDCreatedBase):
    __tablename__ = "person_blocks"
    __table_args__ = (
        CheckConstraint(
            "(blocked_user_id IS NULL) <> (blocked_contact_id IS NULL)",
            name="ck_person_blocks_one_person",
        ),
        # NULLs never collide, so each holds for the kind it names.
        UniqueConstraint(
            "user_id", "pod_id", "blocked_user_id", name="uq_person_blocks_member"
        ),
        UniqueConstraint(
            "user_id", "pod_id", "blocked_contact_id", name="uq_person_blocks_contact"
        ),
        # Who is told of a line: everybody who blocked its sender, by sender.
        Index("ix_person_blocks_pod_member", "pod_id", "blocked_user_id"),
        Index("ix_person_blocks_pod_contact", "pod_id", "blocked_contact_id"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    #: The member who blocked them.
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    blocked_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    blocked_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
