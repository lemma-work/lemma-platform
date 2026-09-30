"""Outside MCP clients: registered clients, grants, and token digests.

Three new tables, none of them read by existing code, so this takes no lock on
anything already serving. `mcp_oauth_clients` is keyed by the client id as
clients send it (a registration id, or the URL of a client metadata document).
`mcp_oauth_grants` holds one live row per person, client and pod, enforced by
a partial unique index so that revoked rows can stay for the record.
`mcp_oauth_tokens` holds SHA-256 digests only.

Revision ID: 0042_mcp_access
Revises: 0041_conversation_last_activity
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0042_mcp_access"
down_revision = "0041_conversation_last_activity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_oauth_clients",
        sa.Column("client_id", sa.String(length=2048), primary_key=True),
        sa.Column("registration", sa.String(length=32), nullable=False),
        sa.Column(
            "client_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("client_secret_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "mcp_oauth_grants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pod_id",
            sa.Uuid(),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            sa.String(length=2048),
            sa.ForeignKey("mcp_oauth_clients.client_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("resource", sa.String(length=2048), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_mcp_oauth_grants_live",
        "mcp_oauth_grants",
        ["user_id", "client_id", "pod_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_mcp_oauth_grants_user", "mcp_oauth_grants", ["user_id", "created_at"]
    )
    op.create_index("ix_mcp_oauth_grants_pod", "mcp_oauth_grants", ["pod_id"])
    op.create_table(
        "mcp_oauth_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "grant_id",
            sa.Uuid(),
            sa.ForeignKey("mcp_oauth_grants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_mcp_oauth_tokens_digest", "mcp_oauth_tokens", ["token_hash"], unique=True
    )
    op.create_index(
        "ix_mcp_oauth_tokens_grant", "mcp_oauth_tokens", ["grant_id", "expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_oauth_tokens_grant", table_name="mcp_oauth_tokens")
    op.drop_index("uq_mcp_oauth_tokens_digest", table_name="mcp_oauth_tokens")
    op.drop_table("mcp_oauth_tokens")
    op.drop_index("ix_mcp_oauth_grants_pod", table_name="mcp_oauth_grants")
    op.drop_index("ix_mcp_oauth_grants_user", table_name="mcp_oauth_grants")
    op.drop_index("uq_mcp_oauth_grants_live", table_name="mcp_oauth_grants")
    op.drop_table("mcp_oauth_grants")
    op.drop_table("mcp_oauth_clients")
