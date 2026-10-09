"""Tables open to people outside the pod: one grant per table.

A row here says a table takes rows from outside -- from its contacts, or from
anyone -- which of its columns they may fill, and which member opened it. Rows
are added as that member, so the grant is never more than they could do
themselves, and it goes with them: deleting the member, or the table, deletes
the grant and the table stops taking rows.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class DatastorePublicRowsModel(UUIDAuditBase):
    __tablename__ = "datastore_public_rows"
    __table_args__ = (
        Index("uq_datastore_public_rows_table", "table_id", unique=True),
        Index("ix_datastore_public_rows_pod", "pod_id"),
        # Deleting a member cascades through this column.
        Index("ix_datastore_public_rows_opened_by", "opened_by"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    table_id: Mapped[UUID] = mapped_column(
        ForeignKey("datastore_tables.id", ondelete="CASCADE"), nullable=False
    )
    #: ``contacts`` or ``anyone``; see ``domain/public_rows.PublicAudience``.
    audience: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The column names people outside may fill, in the order to ask them.
    columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    opened_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
