from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase

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
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    # ACTIVE or DEAD. A session that stopped working is marked rather than
    # deleted, so the person is asked to sign in again before a run fails on it
    # and can still see that the login was once there.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    secret: Mapped[SecretEnvelope] = mapped_column(JSONB, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_hint_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # One saved login per person per site. A second one for the same origin
        # is a replacement, not an addition: an agent picking between two
        # sessions for the same site has no way to pick right.
        UniqueConstraint("user_id", "origin", name="uq_web_logins_user_origin"),
        Index("ix_web_logins_user_id", "user_id"),
    )


class WebLoginAuditModel(UUIDAuditBase):
    """Every time a saved login was used, or changed, and how it went.

    Net-new: nothing in this codebase kept a durable audit trail before, and a
    credential store is the wrong place to discover that. Deliberately its own
    table rather than a log line, because the question it answers — "what has
    been done with my saved logins" — has to survive log retention and be
    answerable to the person whose credentials they are.

    Carries no secret and no page content: which login, which conversation,
    what happened.
    """

    __tablename__ = "web_login_audit"

    web_login_id: Mapped[UUID | None] = mapped_column(
        # Kept when the login is deleted: "it was removed" is exactly the event
        # somebody would come here to find.
        ForeignKey("web_logins.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    origin: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Which agent or function did it, when it was not the person themselves.
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_web_login_audit_user_created", "user_id", "created_at"),
        Index("ix_web_login_audit_login", "web_login_id"),
    )


class SignInRequestModel(UUIDAuditBase):
    """An agent waiting for a person to sign in to a site.

    In Postgres rather than in Redis with a fifteen-minute expiry, because the
    run this belongs to is paused indefinitely -- `PS-AGENT-020` promises a
    pause waits rather than times out. A request that expired underneath a
    conversation that was still waiting would leave the person holding a dead
    link and the agent with nothing to resume from. The browser is the
    ephemeral part of this, and it is re-opened when somebody arrives.

    `tool_call_id` is the paused tool call, which is also the approval id -- so
    finishing a sign-in resolves through the same endpoint and the same durable
    decision row as every other pause a person answers.
    """

    __tablename__ = "web_login_sign_in_requests"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    origin: Mapped[str] = mapped_column(String(255), nullable=False)
    # What the agent was doing, in its own words, shown to the person so they
    # know why they are being asked.
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    conversation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    saved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    saved_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_web_login_sign_in_user_created", "user_id", "created_at"),
        Index("ix_web_login_sign_in_tool_call", "tool_call_id"),
    )
