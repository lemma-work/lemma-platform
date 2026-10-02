"""Tables for contacts: who a pod knows, and the handles it knows them by.

Both cascade from the pod. A contact is personal data the pod holds about its
own customers, so nothing about them outlives the pod that held it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class ContactModel(UUIDAuditBase):
    __tablename__ = "contacts"
    __table_args__ = (Index("ix_contacts_pod_created", "pod_id", "created_at"),)

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ContactIdentityModel(UUIDAuditBase):
    """One handle of one contact.

    Unique per pod and handle: a phone number is one contact in a pod, however
    many of its bots it writes to. ``pod_id`` is repeated from the contact so
    that uniqueness can be a constraint rather than a hope.
    """

    __tablename__ = "contact_identities"
    __table_args__ = (
        UniqueConstraint(
            "pod_id", "kind", "value", name="uq_contact_identities_pod_handle"
        ),
        Index("ix_contact_identities_contact", "contact_id"),
    )

    contact_id: Mapped[UUID] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[str] = mapped_column(String(320), nullable=False)
    strength: Mapped[str] = mapped_column(String(20), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
