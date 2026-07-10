"""Add the backend reliability and durable-delivery foundations.

Revision ID: 0003_backend_reliability
Revises: 0002_surfaces_rework

This intentionally centralizes the release's additive database changes in one
revision: event outbox/inbox, usage-counter repair, schedule-run delivery,
and durable pod-bundle jobs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0003_backend_reliability"
down_revision = "0002_surfaces_rework"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_event_delivery_tables() -> None:
    op.create_table(
        "domain_event_outbox",
        sa.Column("stream", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("producer", sa.String(length=120), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("causation_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(length=160), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_type", sa.String(length=200), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_domain_event_outbox_ready",
        "domain_event_outbox",
        ["available_at", "occurred_at", "id"],
        postgresql_where=sa.text(
            "published_at IS NULL AND dead_lettered_at IS NULL"
        ),
    )
    op.create_index(
        "ix_domain_event_outbox_expired_lease",
        "domain_event_outbox",
        ["lease_until", "occurred_at", "id"],
        postgresql_where=sa.text(
            "lease_until IS NOT NULL AND published_at IS NULL "
            "AND dead_lettered_at IS NULL"
        ),
    )
    op.create_index(
        "ix_domain_event_outbox_published_retention",
        "domain_event_outbox",
        ["published_at"],
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )
    op.create_index(
        "ix_domain_event_outbox_dlq_listing",
        "domain_event_outbox",
        [sa.text("dead_lettered_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("dead_lettered_at IS NOT NULL"),
    )
    op.create_index(
        "ix_domain_event_outbox_occurred",
        "domain_event_outbox",
        [sa.text("occurred_at DESC"), sa.text("id DESC")],
    )

    op.create_table(
        "domain_event_inbox",
        sa.Column("consumer", sa.String(length=200), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="PROCESSING", nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("delivery_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("first_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_type", sa.String(length=200), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "consumer", "event_id", name="uq_domain_event_inbox_consumer_event"
        ),
    )
    op.create_index(
        "ix_domain_event_inbox_status_received",
        "domain_event_inbox",
        ["status", "last_received_at"],
    )
    op.create_index(
        "ix_domain_event_inbox_completed_retention",
        "domain_event_inbox",
        ["completed_at"],
        postgresql_where=sa.text("completed_at IS NOT NULL"),
    )
    op.create_index(
        "ix_domain_event_inbox_dlq_retention",
        "domain_event_inbox",
        ["dead_lettered_at"],
        postgresql_where=sa.text("dead_lettered_at IS NOT NULL"),
    )


def _repair_usage_counter_uniqueness() -> None:
    # PostgreSQL treats NULL values as distinct in a conventional unique index.
    # Collapse legacy duplicates before installing NULLS NOT DISTINCT.
    op.execute(
        """
        WITH aggregates AS (
            SELECT
                min(id::text)::uuid AS keep_id,
                organization_id,
                user_id,
                window_kind,
                window_start,
                max(window_end) AS window_end,
                sum(used_usd) AS used_usd,
                sum(reserved_usd) AS reserved_usd
            FROM usage_limit_counters
            GROUP BY organization_id, user_id, window_kind, window_start
        )
        UPDATE usage_limit_counters AS target
        SET
            window_end = aggregates.window_end,
            used_usd = aggregates.used_usd,
            reserved_usd = aggregates.reserved_usd
        FROM aggregates
        WHERE target.id = aggregates.keep_id
        """
    )
    op.execute(
        """
        WITH keepers AS (
            SELECT
                min(id::text)::uuid AS keep_id,
                organization_id,
                user_id,
                window_kind,
                window_start
            FROM usage_limit_counters
            GROUP BY organization_id, user_id, window_kind, window_start
        )
        DELETE FROM usage_limit_counters AS target
        USING keepers
        WHERE target.organization_id IS NOT DISTINCT FROM keepers.organization_id
          AND target.user_id IS NOT DISTINCT FROM keepers.user_id
          AND target.window_kind = keepers.window_kind
          AND target.window_start = keepers.window_start
          AND target.id <> keepers.keep_id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM usage_limit_counters
                GROUP BY organization_id, user_id, window_kind, window_start
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'usage counter deduplication integrity check failed';
            END IF;
        END $$
        """
    )
    op.drop_index("uq_usage_limit_counter_window", table_name="usage_limit_counters")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_usage_limit_counter_window
        ON usage_limit_counters
        (organization_id, user_id, window_kind, window_start)
        NULLS NOT DISTINCT
        """
    )


def _add_schedule_run_ledger() -> None:
    op.add_column(
        "schedules",
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_table(
        "schedule_runs",
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("source_event_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="RECEIVED", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("target_run_id", sa.String(length=255), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("llm_output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_type", sa.String(length=200), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("source_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "schedule_id", "source_event_id", name="uq_schedule_run_source_event"
        ),
    )
    op.create_index(
        "ix_schedule_runs_schedule_created",
        "schedule_runs",
        ["schedule_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_schedule_runs_retryable_recovery",
        "schedule_runs",
        ["status", "updated_at", "schedule_id"],
        postgresql_where=sa.text("status IN ('RECEIVED', 'PROCESSING', 'FAILED')"),
    )


def _add_pod_bundle_jobs() -> None:
    op.create_table(
        "pod_bundle_jobs",
        sa.Column("job_kind", sa.String(length=16), nullable=False),
        sa.Column("pod_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_step", sa.Integer(), nullable=True),
        sa.Column("committed_steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_type", sa.String(length=200), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["pod_id"], ["pods.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pod_bundle_jobs_active_recovery",
        "pod_bundle_jobs",
        ["job_kind", "status", "heartbeat_at"],
        postgresql_where=sa.text(
            "status IN ('QUEUED', 'FETCHING', 'PLANNING', 'APPLYING', "
            "'CANCELLING', 'EXPORTING', 'PUBLISHING')"
        ),
    )
    op.create_index(
        "ix_pod_bundle_jobs_pod_history",
        "pod_bundle_jobs",
        ["pod_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_pod_bundle_jobs_completed_retention",
        "pod_bundle_jobs",
        ["completed_at"],
        postgresql_where=sa.text("completed_at IS NOT NULL"),
    )

    op.create_table(
        "pod_bundle_job_steps",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_type", sa.String(length=200), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"], ["pod_bundle_jobs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_pod_bundle_job_step",
        "pod_bundle_job_steps",
        ["job_id", "step_index"],
        unique=True,
    )


def _add_agent_invocation_origin() -> None:
    op.add_column(
        "agent_conversations",
        sa.Column("origin_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "agent_conversations",
        sa.Column("origin_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "uq_agent_conversation_origin",
        "agent_conversations",
        ["origin_type", "origin_id"],
        unique=True,
        postgresql_where=sa.text("origin_id IS NOT NULL"),
    )


def upgrade() -> None:
    _add_event_delivery_tables()
    _repair_usage_counter_uniqueness()
    _add_schedule_run_ledger()
    _add_agent_invocation_origin()
    _add_pod_bundle_jobs()


def downgrade() -> None:
    op.drop_table("pod_bundle_job_steps")
    op.drop_table("pod_bundle_jobs")

    op.drop_table("schedule_runs")
    op.drop_column("schedules", "consecutive_failures")

    op.drop_index(
        "uq_agent_conversation_origin",
        table_name="agent_conversations",
    )
    op.drop_column("agent_conversations", "origin_id")
    op.drop_column("agent_conversations", "origin_type")

    op.drop_index("uq_usage_limit_counter_window", table_name="usage_limit_counters")
    op.create_index(
        "uq_usage_limit_counter_window",
        "usage_limit_counters",
        ["organization_id", "user_id", "window_kind", "window_start"],
        unique=True,
    )

    op.drop_table("domain_event_inbox")
    op.drop_table("domain_event_outbox")
