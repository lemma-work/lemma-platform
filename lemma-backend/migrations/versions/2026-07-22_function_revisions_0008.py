"""Add immutable prebuilt function revisions.

Revision ID: 0008_function_revisions
Revises: 0007_auth_hardening
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0008_function_revisions"
down_revision = "0007_auth_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "function_runs",
        "started_at",
        type_=sa.DateTime(timezone=True),
        postgresql_using="started_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "function_runs",
        "completed_at",
        type_=sa.DateTime(timezone=True),
        postgresql_using="completed_at AT TIME ZONE 'UTC'",
    )
    op.add_column(
        "functions",
        sa.Column("active_revision_id", sa.Uuid(), nullable=True),
    )
    op.create_table(
        "function_revisions",
        sa.Column("function_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("code_sha256", sa.String(length=71), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=71), nullable=False),
        sa.Column("artifact_path", sa.String(length=512), nullable=False),
        sa.Column("runtime_abi", sa.String(length=128), nullable=False),
        sa.Column("builder_digest", sa.String(length=256), nullable=False),
        sa.Column(
            "dependency_lock", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotent", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["function_id"], ["functions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "function_id", "artifact_sha256", name="uq_function_revision_artifact"
        ),
        sa.UniqueConstraint(
            "function_id", "revision_number", name="uq_function_revision_number"
        ),
    )
    op.create_index(
        "ix_function_revision_ready",
        "function_revisions",
        ["function_id", "status"],
    )
    op.add_column(
        "function_runs", sa.Column("revision_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "function_runs", sa.Column("current_attempt_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "function_runs",
        sa.Column("execution_fence", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "function_runs",
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_function_runs_revision_id_function_revisions",
        "function_runs",
        "function_revisions",
        ["revision_id"],
        ["id"],
    )
    op.create_table(
        "function_execution_requests",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("pod_id", sa.Uuid(), nullable=False),
        sa.Column("function_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("next_fence", sa.BigInteger(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["function_id"], ["functions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["revision_id"], ["function_revisions.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["function_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pod_id"], ["pods.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index(
        "ix_function_execution_queue",
        "function_execution_requests",
        ["status", "priority", "available_at", "created_at"],
    )
    op.create_index(
        "ix_function_execution_capacity",
        "function_execution_requests",
        ["pod_id", "status", "kind"],
    )
    op.create_table(
        "function_execution_attempts",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("fence", sa.BigInteger(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("ticket_digest", sa.String(length=64), nullable=False),
        sa.Column("runtime_token_digest", sa.String(length=64), nullable=False),
        sa.Column("ticket_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ticket_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_process_id", sa.String(length=256), nullable=True),
        sa.Column("terminal_payload_hash", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"], ["function_execution_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["function_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id"),
        sa.UniqueConstraint("run_id", "fence", name="uq_function_attempt_fence"),
        sa.UniqueConstraint("run_id", "number", name="uq_function_attempt_number"),
        sa.UniqueConstraint("runtime_token_digest"),
        sa.UniqueConstraint("ticket_digest"),
    )
    op.create_index(
        "ix_function_attempt_runtime",
        "function_execution_attempts",
        ["runtime_token_digest"],
    )
    op.create_index(
        "ix_function_attempt_ticket",
        "function_execution_attempts",
        ["ticket_digest"],
    )


def downgrade() -> None:
    op.drop_index("ix_function_attempt_ticket", table_name="function_execution_attempts")
    op.drop_index("ix_function_attempt_runtime", table_name="function_execution_attempts")
    op.drop_table("function_execution_attempts")
    op.drop_index(
        "ix_function_execution_capacity", table_name="function_execution_requests"
    )
    op.drop_index(
        "ix_function_execution_queue", table_name="function_execution_requests"
    )
    op.drop_table("function_execution_requests")
    op.drop_constraint(
        "fk_function_runs_revision_id_function_revisions",
        "function_runs",
        type_="foreignkey",
    )
    op.drop_column("function_runs", "deadline_at")
    op.drop_column("function_runs", "execution_fence")
    op.drop_column("function_runs", "current_attempt_id")
    op.drop_column("function_runs", "revision_id")
    op.drop_index("ix_function_revision_ready", table_name="function_revisions")
    op.drop_table("function_revisions")
    op.drop_column("functions", "active_revision_id")
    op.alter_column(
        "function_runs",
        "completed_at",
        type_=sa.DateTime(timezone=False),
        postgresql_using="completed_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "function_runs",
        "started_at",
        type_=sa.DateTime(timezone=False),
        postgresql_using="started_at AT TIME ZONE 'UTC'",
    )
