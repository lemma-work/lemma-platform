"""connectors revamp: execution descriptor + per-auth-config operation scoping

Adds the polymorphic ``execution`` descriptor column and per-instance operation
scoping (``auth_config_id`` + partial unique indexes) to ``connector_operations``,
and relaxes the single-instance auth-config constraint so multi-instance kinds
(sql/mcp/openapi) can have many auth-configs per (org, connector).

Revision ID: 0010_connectors_kinds
Revises: 0009_agent_host

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_connectors_kinds"
down_revision = "0009_agent_host"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Polymorphic execution descriptor (kind: http/sql/mcp) for package-free ops.
    op.add_column(
        "connector_operations",
        sa.Column("execution", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # Per-instance operation scoping (discovered MCP tools / OpenAPI-URL ops).
    op.add_column(
        "connector_operations",
        sa.Column("auth_config_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_connector_operations_auth_config",
        "connector_operations",
        "auth_configs",
        ["auth_config_id"],
        ["id"],
        ondelete="CASCADE",
    )
    # Replace the baseline unique index with two partial ones (catalog vs per-auth-config).
    op.drop_index("ix_connector_operations_app_provider_name", table_name="connector_operations")
    op.create_index(
        "uq_connector_operations_catalog_name",
        "connector_operations",
        ["connector_id", "provider", "name"],
        unique=True,
        postgresql_where=sa.text("auth_config_id IS NULL"),
    )
    op.create_index(
        "uq_connector_operations_authcfg_name",
        "connector_operations",
        ["connector_id", "provider", "auth_config_id", "name"],
        unique=True,
        postgresql_where=sa.text("auth_config_id IS NOT NULL"),
    )
    op.create_index(
        "ix_connector_operations_auth_config_id",
        "connector_operations",
        ["auth_config_id"],
    )
    # Relax multi-instance: many auth-configs per (org, connector) are allowed;
    # single-instance is enforced in the service layer by capability flag.
    op.drop_index("ix_auth_configs_unique_active_org_app", table_name="auth_configs")


def downgrade() -> None:
    op.create_index(
        "ix_auth_configs_unique_active_org_app",
        "auth_configs",
        ["organization_id", "connector_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.drop_index("ix_connector_operations_auth_config_id", table_name="connector_operations")
    op.drop_index("uq_connector_operations_authcfg_name", table_name="connector_operations")
    op.drop_index("uq_connector_operations_catalog_name", table_name="connector_operations")
    op.create_index(
        "ix_connector_operations_app_provider_name",
        "connector_operations",
        ["connector_id", "provider", "name"],
        unique=True,
    )
    op.drop_constraint(
        "fk_connector_operations_auth_config",
        "connector_operations",
        type_="foreignkey",
    )
    op.drop_column("connector_operations", "auth_config_id")
    op.drop_column("connector_operations", "execution")
