"""Tables a group's people outside the pod may read, or add rows to.

One row per table and group (a GROUP channel): which columns everybody in the
group may read, and which they may fill in a new row, as granted by one member.
Reads and rows go through that member's own access, so a grant is never more
than they can do themselves, and it goes with them: deleting the member, the
table or the group deletes it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class DatastoreChannelGrantModel(UUIDAuditBase):
    __tablename__ = "datastore_channel_grants"
    __table_args__ = (
        Index(
            "uq_datastore_channel_grants_table_channel",
            "table_id",
            "channel_id",
            unique=True,
        ),
        Index("ix_datastore_channel_grants_channel", "channel_id"),
        # Deleting a member cascades through this column.
        Index("ix_datastore_channel_grants_granted_by", "granted_by"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    table_id: Mapped[UUID] = mapped_column(
        ForeignKey("datastore_tables.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    #: The columns everybody in the group may read, in the order to show them.
    read_columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    #: The columns they may fill in a new row; empty when they may only read.
    add_columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    granted_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
