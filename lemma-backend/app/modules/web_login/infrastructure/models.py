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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase, UUIDCreatedBase

# What `core/crypto` writes into an encrypted JSONB column: `_encrypted`, `kid`,
# `alg`, `dek`, `ct` — every value a string (see `core/crypto/envelope.py`).
# Named rather than left as `dict[str, Any]` so the column says what is in it,
# and so a future envelope carrying something other than strings has to come
# past this line.
SecretEnvelope = dict[str, str]


class WebLoginModel(UUIDAuditBase):
    """A saved way back in to one site, for one person.

    ``origin`` is plaintext and indexed while ``secret`` is encrypted JSONB, for
    the reason ``accounts.external_ref`` is: the thing you have to *query* by
    cannot be the thing you encrypt. Choosing which login to inject means asking
    "which row is for this origin", and that question has to be answerable
    without decrypting every row the person owns.
    """

    __tablename__ = "web_logins"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Scheme and host, normalised: `https://app.example.com`. Not a URL — the
    # path a login happens to start at is not what a session belongs to.
    origin: Mapped[str] = mapped_column(String(255), nullable=False)
    # ACTIVE or DEAD. A session that stopped working is marked rather than
    # deleted, so the person is asked to sign in again before a run fails on it
    # and can still see that the login was once there.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    secret: Mapped[SecretEnvelope] = mapped_column(JSONB, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # One saved login per person per site. A second one for the same origin
        # is a replacement, not an addition: an agent picking between two
        # sessions for the same site has no way to pick right.
        UniqueConstraint("user_id", "origin", name="uq_web_logins_user_origin"),
        Index("ix_web_logins_user_id", "user_id"),
    )


class WebLoginAuditModel(UUIDCreatedBase):
    """Every time a saved login was used, or changed, and how it went.

    Net-new: nothing in this codebase kept a durable audit trail before, and a
    credential store is the wrong place to discover that. Deliberately its own
    table rather than a log line, because the question it answers — "what has
    been done with my saved logins" — has to survive log retention and be
    answerable to the person whose credentials they are.

    Carries no secret and no page content, and every column here is written by
    every writer. Three that were not are gone: a foreign key to ``web_logins``
    (NULL on the deletion this trail exists to record), and an ``actor`` string
    that only ever restated ``action`` -- the person asks, captures and deletes;
    an agent injects. ``conversation_id`` says which run far more precisely than
    a label would have.

    ``UUIDCreatedBase`` rather than ``UUIDAuditBase``: appended to and never
    updated, so there is no honest value for ``updated_at`` to hold.
    """

    __tablename__ = "web_login_audit"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: Which run did it. NULL for the things a person does themselves, from the
    #: saved-logins screen rather than from inside a conversation.
    conversation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    origin: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Why, in the platform's own words, when the outcome was not plain "ok".
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_web_login_audit_user_created", "user_id", "created_at"),
    )
