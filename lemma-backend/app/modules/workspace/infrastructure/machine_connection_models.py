"""The codes that pair connected machines; the connections themselves are
``machine_models.MachineConnectionModel``."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDCreatedBase


class MachineConnectionPairingModel(UUIDCreatedBase):
    """A code a machine asked for, until it is claimed or expires.

    Approving fills ``connection_id``; the machine's claim deletes the row. So
    a code is single-use by presence, as Agent Host pairing codes are.
    """

    __tablename__ = "machine_connection_pairings"
    __table_args__ = (
        UniqueConstraint("code_hash", name="uq_machine_connection_pairings_code"),
        Index("ix_machine_connection_pairings_expires_at", "expires_at"),
    )

    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("machine_connections.id", ondelete="CASCADE"), nullable=True
    )
