"""Tables people outside the pod may read: one grant per table.

A row here says a table may be read from outside -- by its confirmed contacts,
or by anyone -- which of its columns, in which order, and which member opened
it. Rows are read as that member, so the grant is never more than they could
read themselves, and it goes with them: deleting the member, or the table,
deletes the grant and the table stops being read.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class DatastorePublicReadsModel(UUIDAuditBase):
    __tablename__ = "datastore_public_reads"
    __table_args__ = (
        Index("uq_datastore_public_reads_table", "table_id", unique=True),
        Index("ix_datastore_public_reads_pod", "pod_id"),
        # Deleting a member cascades through this column.
        Index("ix_datastore_public_reads_opened_by", "opened_by"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    table_id: Mapped[UUID] = mapped_column(
        ForeignKey("datastore_tables.id", ondelete="CASCADE"), nullable=False
    )
    #: ``contacts`` or ``anyone``; see ``domain/public_rows.PublicAudience``.
    audience: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The column names people outside may see, in the order to show them.
    columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    #: The open column the rows are read in ascending order of, if any.
    order_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    opened_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
