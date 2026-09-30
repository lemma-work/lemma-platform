"""Tables for outside MCP clients: who registered, who was let in, with what.

Tokens are stored as SHA-256 digests and never in the clear. A token is 256
random bits, so a plain digest is enough: there is nothing to brute-force, and
a salted slow hash would only cost a lookup on every MCP request.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base, UUIDAuditBase, UUIDCreatedBase


class McpOAuthClient(Base):
    """A client that registered, or whose metadata document was fetched.

    ``client_id`` is the key rather than a surrogate id because it is what every
    request names: a random id for a dynamically registered client, the
    document's URL for a metadata-document client. ``client_metadata`` holds the
    RFC 7591 fields as the client sent them, minus any secret.
    """

    __tablename__ = "mcp_oauth_clients"

    client_id: Mapped[str] = mapped_column(String(2048), primary_key=True)
    registration: Mapped[str] = mapped_column(String(32), nullable=False)
    client_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    client_secret_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class McpOAuthGrant(UUIDAuditBase):
    """One person letting one client use one pod.

    At most one live grant per (user, client, pod): consenting again widens or
    narrows the scopes of the grant already there rather than adding a second
    row to the person's connected-apps list.
    """

    __tablename__ = "mcp_oauth_grants"
    __table_args__ = (
        Index(
            "uq_mcp_oauth_grants_live",
            "user_id",
            "client_id",
            "pod_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_mcp_oauth_grants_user", "user_id", "created_at"),
        Index("ix_mcp_oauth_grants_pod", "pod_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("mcp_oauth_clients.client_id", ondelete="CASCADE"),
        nullable=False,
    )
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    resource: Mapped[str] = mapped_column(String(2048), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class McpOAuthToken(UUIDCreatedBase):
    """An access or refresh token, by digest.

    A rotated refresh token is kept, marked ``rotated_at``, until it expires: a
    second presentation of it means two parties hold it, and the answer to that
    (OAuth 2.1 §4.3.1) is to end the grant, which needs the old row to know.
    """

    __tablename__ = "mcp_oauth_tokens"
    __table_args__ = (
        Index("uq_mcp_oauth_tokens_digest", "token_hash", unique=True),
        Index("ix_mcp_oauth_tokens_grant", "grant_id", "expires_at"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    grant_id: Mapped[UUID] = mapped_column(
        ForeignKey("mcp_oauth_grants.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
