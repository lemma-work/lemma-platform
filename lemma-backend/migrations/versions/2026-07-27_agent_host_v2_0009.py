"""Add durable external Agent Host v2 control-plane state.

Revision ID: 0009_agent_host_v2
Revises: 0008_function_execution
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0009_agent_host_v2"
down_revision = "0008_function_execution"
branch_labels = None
depends_on = None


UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _created_columns() -> list[sa.Column]:
    return [
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "agent_host_pairings",
        *_created_columns(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash", name="uq_agent_host_pairing_code_hash"),
    )
    op.create_index(
        "ix_agent_host_pairing_user_expires",
        "agent_host_pairings",
        ["user_id", "expires_at"],
    )
    op.create_index(
        "ix_agent_host_pairings_user_id",
        "agent_host_pairings",
        ["user_id"],
    )
    op.create_index(
        "ix_agent_host_pairings_organization_id",
        "agent_host_pairings",
        ["organization_id"],
    )

    op.create_table(
        "agent_hosts",
        *_audit_columns(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=True),
        sa.Column("installation_id", sa.String(length=255), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("public_key_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("protocol_min", sa.Integer(), nullable=False),
        sa.Column("protocol_max", sa.Integer(), nullable=False),
        sa.Column("protocol_version", sa.Integer(), nullable=True),
        sa.Column("host_release", sa.String(length=128), nullable=False),
        sa.Column("adapter_manifest_id", sa.String(length=255), nullable=False),
        sa.Column("instance_id", UUID, nullable=True),
        sa.Column("capacity", JSONB, nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "organization_id",
            "installation_id",
            name="uq_agent_host_user_org_installation",
            postgresql_nulls_not_distinct=True,
        ),
        sa.UniqueConstraint(
            "organization_id",
            "public_key_fingerprint",
            name="uq_agent_host_org_public_key_fingerprint",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_agent_hosts_user_id", "agent_hosts", ["user_id"])
    op.create_index(
        "ix_agent_hosts_organization_id", "agent_hosts", ["organization_id"]
    )
    op.create_index("ix_agent_hosts_status", "agent_hosts", ["status"])
    op.create_index("ix_agent_host_user_status", "agent_hosts", ["user_id", "status"])

    op.create_table(
        "agent_host_integrations",
        *_audit_columns(),
        sa.Column("host_id", UUID, nullable=False),
        sa.Column("integration_key", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("adapter_protocol", sa.String(length=32), nullable=False),
        sa.Column("adapter_version", sa.String(length=128), nullable=False),
        sa.Column("upstream_version", sa.String(length=128), nullable=True),
        sa.Column("auth_state", sa.String(length=64), nullable=False),
        sa.Column("health", sa.String(length=64), nullable=False),
        sa.Column("capabilities", JSONB, nullable=False),
        sa.Column("config_revision", sa.String(length=255), nullable=False),
        sa.Column("config_options", JSONB, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stale_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stale_reason", sa.Text(), nullable=True),
        sa.Column("integration_metadata", JSONB, nullable=False),
        sa.ForeignKeyConstraint(["host_id"], ["agent_hosts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "host_id",
            "integration_key",
            name="uq_agent_host_integration_key",
        ),
    )
    op.create_index(
        "ix_agent_host_integrations_host_id",
        "agent_host_integrations",
        ["host_id"],
    )
    op.create_index(
        "ix_agent_host_integrations_health",
        "agent_host_integrations",
        ["health"],
    )
    op.create_index(
        "ix_agent_host_integration_host_health",
        "agent_host_integrations",
        ["host_id", "health"],
    )

    op.add_column(
        "agent_runtime_profiles",
        sa.Column("host_integration_id", UUID, nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_runtime_profiles_host_integration",
        "agent_runtime_profiles",
        "agent_host_integrations",
        ["host_integration_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_agent_runtime_profiles_host_integration_id",
        "agent_runtime_profiles",
        ["host_integration_id"],
    )

    op.create_table(
        "agent_host_commands",
        *_created_columns(),
        sa.Column("host_id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=True),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["host_id"], ["agent_hosts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_host_commands_host_id", "agent_host_commands", ["host_id"]
    )
    op.create_index("ix_agent_host_commands_run_id", "agent_host_commands", ["run_id"])
    op.create_index("ix_agent_host_commands_state", "agent_host_commands", ["state"])
    op.create_index(
        "ix_agent_host_command_poll",
        "agent_host_commands",
        ["host_id", "state", "created_at"],
    )
    op.create_index(
        "ix_agent_host_command_run",
        "agent_host_commands",
        ["run_id", "lease_epoch"],
    )

    op.create_table(
        "agent_host_run_leases",
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("host_id", UUID, nullable=False),
        sa.Column("integration_id", UUID, nullable=False),
        sa.Column("runtime_profile_id", UUID, nullable=True),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("checkpoint", sa.String(length=32), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "acked_event_sequence",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["host_id"], ["agent_hosts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["integration_id"], ["agent_host_integrations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["runtime_profile_id"],
            ["agent_runtime_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "ix_agent_host_run_leases_host_id",
        "agent_host_run_leases",
        ["host_id"],
    )
    op.create_index(
        "ix_agent_host_run_leases_state",
        "agent_host_run_leases",
        ["state"],
    )
    op.create_index(
        "ix_agent_host_run_lease_host_state",
        "agent_host_run_leases",
        ["host_id", "state"],
    )
    op.create_index(
        "ix_agent_host_run_lease_expiry",
        "agent_host_run_leases",
        ["lease_expires_at"],
        postgresql_where=sa.text(
            "state NOT IN ('WAITING_INPUT','SUCCEEDED','FAILED',"
            "'CANCELLED','DISPATCH_UNKNOWN')"
        ),
    )

    op.create_table(
        "agent_host_events",
        *_created_columns(),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.String(length=255), nullable=True),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("integration_key", sa.String(length=128), nullable=False),
        sa.Column("adapter_version", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "lease_epoch",
            "sequence",
            name="uq_agent_host_event_sequence",
        ),
        sa.UniqueConstraint("event_id", name="uq_agent_host_event_id"),
    )
    op.create_index("ix_agent_host_events_run_id", "agent_host_events", ["run_id"])
    op.create_index(
        "ix_agent_host_event_consume",
        "agent_host_events",
        ["run_id", "sequence"],
    )

    op.create_table(
        "agent_host_auth_nonces",
        *_created_columns(),
        sa.Column("host_id", UUID, nullable=False),
        sa.Column("nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["host_id"], ["agent_hosts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("host_id", "nonce_hash", name="uq_agent_host_auth_nonce"),
    )
    op.create_index(
        "ix_agent_host_auth_nonces_host_id",
        "agent_host_auth_nonces",
        ["host_id"],
    )
    op.create_index(
        "ix_agent_host_auth_nonce_expires",
        "agent_host_auth_nonces",
        ["expires_at"],
    )

    op.create_table(
        "agent_host_mcp_routes",
        *_created_columns(),
        sa.Column("host_id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        sa.Column("encrypted_payload", JSONB, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["host_id"], ["agent_hosts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_agent_host_mcp_route_run"),
    )
    op.create_index(
        "ix_agent_host_mcp_routes_host_id",
        "agent_host_mcp_routes",
        ["host_id"],
    )
    op.create_index(
        "ix_agent_host_mcp_routes_run_id",
        "agent_host_mcp_routes",
        ["run_id"],
    )
    op.create_index(
        "ix_agent_host_mcp_route_host_expiry",
        "agent_host_mcp_routes",
        ["host_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_table("agent_host_mcp_routes")
    op.drop_table("agent_host_auth_nonces")
    op.drop_table("agent_host_events")
    op.drop_table("agent_host_run_leases")
    op.drop_table("agent_host_commands")
    op.drop_index(
        "ix_agent_runtime_profiles_host_integration_id",
        table_name="agent_runtime_profiles",
    )
    op.drop_constraint(
        "fk_agent_runtime_profiles_host_integration",
        "agent_runtime_profiles",
        type_="foreignkey",
    )
    op.drop_column("agent_runtime_profiles", "host_integration_id")
    op.drop_table("agent_host_integrations")
    op.drop_table("agent_hosts")
    op.drop_table("agent_host_pairings")
