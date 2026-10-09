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
    Integer,
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
    """One person letting one client use one pod: one consent, one connection.

    Not unique per (user, client, pod). The same client on two devices is two
    connections, listed and ended separately; one grant shared between them
    meant disconnecting either -- or a replay on either -- ended both.
    """

    __tablename__ = "mcp_oauth_grants"
    __table_args__ = (
        Index(
            "ix_mcp_oauth_grants_connection",
            "user_id",
            "client_id",
            "pod_id",
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

    A rotated refresh token is marked ``rotated_at``: a second presentation of
    it means two parties hold it, and the answer to that (OAuth 2.1 §4.3.1) is
    to end the grant, which needs the old row to know. Each rotation keeps only
    the token it just rotated, so a grant holds a handful of rows.

    ``superseded_at`` marks a refresh token a retried refresh cancelled. Those
    are kept until they expire whatever later rotations prune: the party left
    holding one is the one that lost the race, and presenting it later has to
    end the grant rather than find nothing.
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
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class McpEventSubscription(UUIDAuditBase):
    """One outside client's subscription to one pod event, delivered by webhook.

    Its identity is `(grant, url, name, arguments)`, the draft's: subscribing
    again with the same four refreshes this row rather than adding one. The
    grant's, so ending the connection ends it -- deleted with the grant, and
    refused at delivery the moment the grant is revoked. The client's signing
    secret is stored encrypted; it is what makes a delivery believable to the
    receiver, and it is never shown back.

    `stopped_at` is the person's Stop, kept as a tombstone so the client's next
    refresh is refused instead of re-creating it. `paused_at` is delivery
    giving up on a receiver that kept failing, until the client refreshes.
    """

    __tablename__ = "mcp_event_subscriptions"
    __table_args__ = (
        Index(
            "uq_mcp_event_subscriptions_identity",
            "grant_id",
            "url",
            "name",
            "arguments_key",
            unique=True,
        ),
        Index("ix_mcp_event_subscriptions_pod_name", "pod_id", "name"),
    )

    public_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    grant_id: Mapped[UUID] = mapped_column(
        ForeignKey("mcp_oauth_grants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    arguments: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    arguments_key: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_before: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
