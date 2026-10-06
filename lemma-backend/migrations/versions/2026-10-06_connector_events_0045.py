"""A connected MCP server's events, and the subscriptions standing work made.

**`auth_config_events`** is what an MCP install's server lists with
`events/list`, kept per install beside `auth_config_operations` and for the
same reason: a server's events describe the tenant's own systems, so they stay
out of the global trigger catalog. Re-discovery upserts by
`(auth_config_id, name)` and then drops what the server stopped listing.

**`connector_event_subscriptions`** is one row per subscription made on a
person's account for a schedule: which event, with which arguments, signed
with which secret (ours, stored encrypted), and until when the server granted
it. Its `id` rides in the callback URL, which is how a delivery -- and the
challenge that arrives before the server has named its own id -- finds the
secret to verify against. `CASCADE` on the account and the install: a
disconnected account leaves nothing that could still verify a delivery.

New and empty; nothing is backfilled.

Revision ID: 0045_connector_events
Revises: 0044_mcp_event_subscriptions
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045_connector_events"
down_revision = "0044_mcp_event_subscriptions"
branch_labels = None
depends_on = None


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _owner_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "auth_config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("auth_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "auth_config_events",
        *_audit_columns(),
        *_owner_columns(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_schema", postgresql.JSONB(), nullable=False),
        sa.Column("payload_schema", postgresql.JSONB(), nullable=False),
    )
    op.create_index(
        "uq_auth_config_events_name",
        "auth_config_events",
        ["auth_config_id", "name"],
        unique=True,
    )
    op.create_index(
        "ix_auth_config_events_org", "auth_config_events", ["organization_id"]
    )

    op.create_table(
        "connector_event_subscriptions",
        *_audit_columns(),
        *_owner_columns(),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("remote_id", sa.String(255), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_connector_event_subscriptions_refresh",
        "connector_event_subscriptions",
        ["refresh_before"],
    )
    op.create_index(
        "ix_connector_event_subscriptions_account",
        "connector_event_subscriptions",
        ["account_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_connector_event_subscriptions_account",
        table_name="connector_event_subscriptions",
    )
    op.drop_index(
        "ix_connector_event_subscriptions_refresh",
        table_name="connector_event_subscriptions",
    )
    op.drop_table("connector_event_subscriptions")
    op.drop_index("ix_auth_config_events_org", table_name="auth_config_events")
    op.drop_index("uq_auth_config_events_name", table_name="auth_config_events")
    op.drop_table("auth_config_events")
