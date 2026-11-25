"""Hooks: an address a service outside the pod posts to, one row per delivery.

A row here is one hook: the table its deliveries are written to, how a body
becomes a row (``fields``), and the hash of the secret its address carries --
never the secret itself. Rows are added as the member who made the hook, so a
hook is never more than they could do themselves, and it goes with them, with
its table and with its pod.

The last delivery and the last refusal are kept on the hook rather than in a
log: the owner needs to know whether the service is reaching it and, when it is
not, why -- and one row per hook answers both without growing.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class DatastoreHookModel(UUIDAuditBase):
    __tablename__ = "datastore_hooks"
    __table_args__ = (
        Index("uq_datastore_hooks_pod_name", "pod_id", "name", unique=True),
        Index("ix_datastore_hooks_table", "table_id"),
        # Deleting a member cascades through this column.
        Index("ix_datastore_hooks_created_by", "created_by"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    table_id: Mapped[UUID] = mapped_column(
        ForeignKey("datastore_tables.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    #: SHA-256 of the secret, hex. The secret is shown once and never stored.
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    #: Column name -> path into the body; null writes the whole body to
    #: ``payload``. See ``domain/hooks``.
    fields: Mapped[dict[str, str] | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    last_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
