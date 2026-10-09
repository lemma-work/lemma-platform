"""Outside clients' subscriptions to a pod's events.

**`mcp_event_subscriptions`** is one row per subscription an outside MCP client
(ChatGPT) made with `events/subscribe`: which connection, which event, with
which arguments, delivered to which callback URL, signed with which secret.
Its identity is `(grant_id, url, name, arguments_key)`, the working-group
draft's, so subscribing again with the same four refreshes the row; the unique
index is what makes that an upsert. `public_id` is the `sub_...` id the client
sees, derived from the identity. The client's signing secret is stored
encrypted. `CASCADE` on the grant: a connection removed takes its
subscriptions with it, and delivery refuses one whose grant is revoked.

New and empty; nothing is backfilled.

Revision ID: 0047_mcp_event_subscriptions
Revises: 0046_decisions_adoption
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0047_mcp_event_subscriptions"
down_revision = "0046_decisions_adoption"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_event_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "grant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mcp_oauth_grants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pod_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column("arguments_key", sa.String(64), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("refresh_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index(
        "uq_mcp_event_subscriptions_identity",
        "mcp_event_subscriptions",
        ["grant_id", "url", "name", "arguments_key"],
        unique=True,
    )
    op.create_index(
        "ix_mcp_event_subscriptions_pod_name",
        "mcp_event_subscriptions",
        ["pod_id", "name"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mcp_event_subscriptions_pod_name", table_name="mcp_event_subscriptions"
    )
    op.drop_index(
        "uq_mcp_event_subscriptions_identity", table_name="mcp_event_subscriptions"
    )
    op.drop_table("mcp_event_subscriptions")
