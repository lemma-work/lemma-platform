"""Add Agent Host identity tables (pairings, hosts, harnesses).

Additive only. This revision introduces the Agent Host *identity* surface — the
tables needed to pair a machine and publish its harness snapshots. It does not
touch ``agent_runtime_profiles`` and does not remove the legacy local daemon, so
the existing runtime keeps working unchanged and this revision downgrades
losslessly.

Dispatch state (commands, run leases) and runtime-profile unification land in the
following revision; the legacy daemon is removed in a later cleanup revision once
Agent Host is proven end to end.

Revision ID: 0009_agent_host_identity
Revises: 0008_function_execution
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0009_agent_host_identity"
down_revision = "0008_function_execution"
branch_labels = None
depends_on = None


UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())

# uq_agent_host_user_org_installation relies on NULLS NOT DISTINCT so that a
# personal host (organization_id IS NULL) still collides with itself on
# re-pairing. That clause is PostgreSQL 15+ only, and on older servers the
# constraint would silently permit duplicate personal installations.
_MINIMUM_POSTGRES_VERSION = 150000


def _created_columns() -> list[sa.Column]:
    return [
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _audit_columns() -> list[sa.Column]:
    return [
        *_created_columns(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _require_supported_postgres() -> None:
    version = op.get_bind().scalar(sa.text("SHOW server_version_num"))
    if int(version) < _MINIMUM_POSTGRES_VERSION:
        raise RuntimeError(
            "Agent Host requires PostgreSQL 15 or newer for NULLS NOT DISTINCT "
            f"unique constraints; this server reports server_version_num={version}"
        )


def upgrade() -> None:
    _require_supported_postgres()

    op.create_table(
        "agent_host_pairings",
        *_created_columns(),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("organization_id", UUID, nullable=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash", name="uq_agent_host_pairing_code_hash"),
    )
    # (user_id, expires_at) also serves lookups keyed on user_id alone, so no
    # separate single-column index is created here.
    op.create_index(
        "ix_agent_host_pairing_user_expires",
        "agent_host_pairings",
        ["user_id", "expires_at"],
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
        sa.Column("host_secret_hash", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("protocol_version", sa.Integer(), nullable=True),
        sa.Column("host_release", sa.String(length=128), nullable=False),
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
        sa.UniqueConstraint("host_secret_hash", name="uq_agent_host_secret_hash"),
    )
    op.create_index(
        "ix_agent_hosts_organization_id", "agent_hosts", ["organization_id"]
    )
    # Status-only lookups drive the offline sweep across all users, so this is
    # not covered by (user_id, status) below.
    op.create_index("ix_agent_hosts_status", "agent_hosts", ["status"])
    op.create_index("ix_agent_host_user_status", "agent_hosts", ["user_id", "status"])

    op.create_table(
        "agent_host_harnesses",
        *_audit_columns(),
        sa.Column("host_id", UUID, nullable=False),
        sa.Column("harness_key", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("adapter_version", sa.String(length=128), nullable=False),
        sa.Column("upstream_version", sa.String(length=128), nullable=True),
        sa.Column("health", sa.String(length=64), nullable=False),
        sa.Column("capabilities", JSONB, nullable=False),
        sa.Column("config_revision", sa.String(length=255), nullable=False),
        sa.Column("config_options", JSONB, nullable=False),
        sa.Column("stale_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stale_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["host_id"], ["agent_hosts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("host_id", "harness_key", name="uq_agent_host_harness_key"),
    )
    op.create_index(
        "ix_agent_host_harnesses_host_id", "agent_host_harnesses", ["host_id"]
    )


def downgrade() -> None:
    op.drop_table("agent_host_harnesses")
    op.drop_table("agent_hosts")
    op.drop_table("agent_host_pairings")
