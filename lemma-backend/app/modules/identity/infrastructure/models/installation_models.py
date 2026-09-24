"""Who owns this installation, on a deployment that has one owner.

One row or none, enforced by the table rather than by the code that writes it:
the primary key is a boolean that a check constraint pins to true, so a second
row is not a race to lose but a statement the database refuses. That is what
makes "the first account becomes the owner" hold under two concurrent signups
-- both may try, exactly one `INSERT` lands, and the other sees the conflict.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base


class InstallationOwner(Base):
    __tablename__ = "installation_owner"
    __table_args__ = (
        CheckConstraint("singleton", name="ck_installation_owner_singleton"),
    )

    singleton: Mapped[bool] = mapped_column(
        Boolean, primary_key=True, default=True, server_default=text("true")
    )
    # The address that claimed the slot. Kept after the account is bound so a
    # retried first signup from the same address is recognised as the same
    # person rather than as a second one.
    email: Mapped[str] = mapped_column(String(255))
    # Null while the claim is only a reservation: the slot is taken before the
    # account exists, so there is a window where the owner is an address and
    # not yet a user. `SET NULL` rather than `CASCADE` on delete, deliberately:
    # a vanished owner must leave the slot *taken*, not free it for whoever
    # signs up next -- that would hand the host-execution privilege the owner
    # carries to a stranger who happened to arrive after a deletion.
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
