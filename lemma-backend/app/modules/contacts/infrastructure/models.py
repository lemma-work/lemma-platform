"""Tables for contacts: who a pod knows, and the handles it knows them by.

Both cascade from the pod. A contact is personal data the pod holds about its
own customers, so nothing about them outlives the pod that held it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base, UUIDAuditBase


class ContactModel(UUIDAuditBase):
    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_pod_created", "pod_id", "created_at"),
        Index("ix_contacts_merged_into", "merged_into_id"),
        # One person or the other: a contact is never both another contact
        # and a member at once.
        CheckConstraint(
            "merged_into_id IS NULL OR merged_into_user_id IS NULL",
            name="ck_contacts_merged_once",
        ),
        # What the organization's contact limit counts, before every turn.
        Index(
            "ix_contacts_answered",
            "pod_id",
            postgresql_where=text("answered_at IS NOT NULL AND merged_into_id IS NULL"),
        ),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The contact this one turned out to be. Always one hop: merging into a
    #: contact that was itself merged points at where that one went. Forgetting
    #: the survivor forgets the contacts that became it.
    merged_into_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    #: The member this contact proved to be (``services/member_folds``): the
    #: row stays, holding no handles, so a line or a conversation still naming
    #: it says it was this person, outside the pod, then.
    merged_into_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_contacts_merged_into_user"
        ),
        nullable=True,
    )
    #: When an agent first answered them: what the organization's contact
    #: limit counts, so a crowded group nobody spoke to the bot in costs nothing.
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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
    #: When the contact last wrote from this handle. WhatsApp allows a business
    #: to write freely only within 24 hours of it.
    last_inbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The product that named a host identity (``host:{issuer}:{sub}``), so
    #: it can be read back without parsing the value. ``None`` for every other
    #: kind.
    issuer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The handle as it arrived, before normalising: what a member reads.
    value_raw: Mapped[str | None] = mapped_column(String(320), nullable=True)


class ContactSuppressionModel(Base):
    """A handle nobody in the pod may write to: it said stop, or bounced.

    Keyed by a hash of the handle, not the contact, so it outlives the contact
    being forgotten -- a person who said stop is not written to again because
    the pod forgot who they were.
    """

    __tablename__ = "contact_suppressions"

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    #: sha256 of the normalised handle.
    value_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    #: ``stop`` (they asked), ``unsubscribe`` (the email link), ``bounce``.
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
