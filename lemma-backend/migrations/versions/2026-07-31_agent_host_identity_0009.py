"""Add the Agent Host schema.

This is the single migration for the whole Agent Host feature: identity
(pairings, hosts, harnesses), run dispatch (commands, run leases), and the
runtime-profile columns that bind a profile to a harness. Deployments therefore
apply one revision, not a chain of them.

It is deliberately additive — no drops, deletes, or renames. The legacy local
daemon keeps its tables and columns and keeps working; the daemon is retired in
code, and its now-unused ``agent_runtime_daemons`` table and the obsolete
``protocol``/``kind``/``daemon_id``/``profile_metadata`` columns are left in
place for a later, unrelated cleanup. Leaving an empty table behind costs
nothing and buys a lossless downgrade plus a main branch that stays releasable
at every commit of the rollout.

Run events are not journaled here. They travel a per-run Redis Stream, so there
is no ``agent_host_events`` table and no ack-watermark column on the lease: the
stream's last entry is the watermark, and a host that resends after a Redis
flush is deduplicated by sequence.

Revision ID: 0009_agent_host
Revises: 0008_function_execution
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0009_agent_host"
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


# Every declarative base used to declare `id` as primary_key=True AND
# index=True, so each table carried a plain btree on `id` on top of the unique
# index PostgreSQL already builds for the primary key. The duplicate was
# maintained on every insert and could never be chosen over the PK index. The
# bases no longer set it; this drops the ones already in the schema.
#
# The drop is derived at runtime so it matches whatever a given environment
# actually has: single-column, non-unique, non-primary indexes on `id`.
_REDUNDANT_ID_INDEXES = sa.text(
    """
    SELECT c.relname AS index_name, t.relname AS table_name
      FROM pg_index x
      JOIN pg_class c ON c.oid = x.indexrelid
      JOIN pg_class t ON t.oid = x.indrelid
      JOIN pg_namespace n ON n.oid = t.relnamespace
     WHERE n.nspname = current_schema()
       AND NOT x.indisprimary
       AND NOT x.indisunique
       AND x.indnatts = 1
       AND (SELECT attname FROM pg_attribute
             WHERE attrelid = t.oid AND attnum = x.indkey[0]) = 'id'
    """
)


def _drop_redundant_id_indexes() -> None:
    for index_name, _table in op.get_bind().execute(_REDUNDANT_ID_INDEXES):
        op.execute(sa.text(f'DROP INDEX IF EXISTS "{index_name}"'))


# The exact set of tables carrying ix_<table>_id at revision 0008. Pinned
# rather than derived, so a downgrade restores precisely what was there:
# five tables with an id primary key never had the duplicate.
_TABLES_WITH_REDUNDANT_ID_INDEX = (
    "accounts",
    "agent_approval_decisions",
    "agent_conversations",
    "agent_feedback",
    "agent_messages",
    "agent_runs",
    "agent_runtime_daemons",
    "agent_runtime_profiles",
    "agent_surface_conversation_links",
    "agent_surface_external_users",
    "agent_surfaces",
    "agents",
    "app_releases",
    "apps",
    "auth_configs",
    "auth_permissions",
    "connect_requests",
    "connector_operations",
    "connector_triggers",
    "connectors",
    "datastore_files",
    "datastore_tables",
    "function_runs",
    "functions",
    "organization_invitations",
    "organization_members",
    "organizations",
    "pod_join_requests",
    "pod_members",
    "pods",
    "resource_permission_grants",
    "role_assignments",
    "role_permissions",
    "roles",
    "schedules",
    "usage_limit_counters",
    "usage_records",
    "users",
    "workflow_flow_runs",
    "workflow_flows",
    "workflow_run_waits",
)


def _restore_redundant_id_indexes() -> None:
    """Recreate the duplicates so the downgrade is faithful."""
    for table_name in _TABLES_WITH_REDUNDANT_ID_INDEX:
        op.execute(
            sa.text(
                f'CREATE INDEX IF NOT EXISTS "ix_{table_name}_id" '
                f'ON "{table_name}" (id)'
            )
        )


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

    op.create_table(
        "agent_host_commands",
        *_created_columns(),
        sa.Column("host_id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=True),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection", JSONB, nullable=True),
        sa.ForeignKeyConstraint(["host_id"], ["agent_hosts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Drives the competitive FOR UPDATE SKIP LOCKED handout in poll_commands.
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

    # run_id is the primary key, which is what makes double-dispatch of a run
    # structurally impossible rather than merely guarded in application code.
    op.create_table(
        "agent_host_run_leases",
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("host_id", UUID, nullable=False),
        sa.Column("harness_id", UUID, nullable=False),
        sa.Column("runtime_profile_id", UUID, nullable=True),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["host_id"], ["agent_hosts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["harness_id"], ["agent_host_harnesses.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["runtime_profile_id"],
            ["agent_runtime_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    # (host_id, state) also serves host_id-only lookups.
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

    # Profile binding. Both columns are nullable so existing rows, including
    # legacy daemon profiles, stay valid without a backfill.
    op.add_column(
        "agent_runtime_profiles",
        sa.Column("runtime_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "agent_runtime_profiles",
        sa.Column("harness_id", UUID, nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_runtime_profiles_harness",
        "agent_runtime_profiles",
        "agent_host_harnesses",
        ["harness_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_agent_runtime_profiles_harness_id",
        "agent_runtime_profiles",
        ["harness_id"],
    )
    # Holds for legacy rows too: runtime_type IS NULL takes the first branch,
    # which only requires harness_id to be NULL.
    op.create_check_constraint(
        "ck_agent_runtime_profile_harness_binding",
        "agent_runtime_profiles",
        "(runtime_type IS DISTINCT FROM 'HARNESS' AND harness_id IS NULL) OR "
        "(runtime_type = 'HARNESS' AND harness_id IS NOT NULL)",
    )

    _drop_redundant_id_indexes()


def downgrade() -> None:
    _restore_redundant_id_indexes()

    op.drop_constraint(
        "ck_agent_runtime_profile_harness_binding",
        "agent_runtime_profiles",
        type_="check",
    )
    op.drop_index(
        "ix_agent_runtime_profiles_harness_id",
        table_name="agent_runtime_profiles",
    )
    op.drop_constraint(
        "fk_agent_runtime_profiles_harness",
        "agent_runtime_profiles",
        type_="foreignkey",
    )
    op.drop_column("agent_runtime_profiles", "harness_id")
    op.drop_column("agent_runtime_profiles", "runtime_type")

    op.drop_table("agent_host_run_leases")
    op.drop_table("agent_host_commands")
    op.drop_table("agent_host_harnesses")
    op.drop_table("agent_hosts")
    op.drop_table("agent_host_pairings")
