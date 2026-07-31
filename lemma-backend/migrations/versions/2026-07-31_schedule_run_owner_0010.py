"""Give every schedule run one canonical user owner.

Revision ID: 0010_schedule_run_owner
Revises: 0009_agent_host
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision = "0010_schedule_run_owner"
down_revision = "0009_agent_host"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "schedule_runs",
        sa.Column("user_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        """
        UPDATE schedule_runs AS run
        SET user_id = schedule.user_id
        FROM schedules AS schedule
        WHERE schedule.id = run.schedule_id
          AND run.user_id IS NULL
        """
    )
    op.alter_column("schedule_runs", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_schedule_runs_user_id_users",
        "schedule_runs",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_schedule_runs_user_id", "schedule_runs", ["user_id"])

    # Every logical fire reserves its workflow-run/conversation ID before launch.
    # Existing dispatched rows already carry the real target ID; failed deliveries
    # without a target get a reservation for any automatic redelivery.
    op.execute(
        """
        UPDATE schedule_runs
        SET target_run_id = gen_random_uuid()::text
        WHERE target_run_id IS NULL
        """
    )
    op.alter_column("schedule_runs", "target_run_id", nullable=False)
    op.create_index(
        "uq_schedule_runs_target",
        "schedule_runs",
        ["target_kind", "target_run_id"],
        unique=True,
    )
    op.execute("DROP INDEX IF EXISTS ix_schedule_runs_retryable_recovery")

    # Repair the schedule ledger from the authoritative target rows. DISPATCHED
    # now means the target is still active, so it intentionally has no completed_at.
    op.execute(
        """
        UPDATE schedule_runs AS run
        SET status = CASE workflow.status
                WHEN 'COMPLETED' THEN 'COMPLETED'
                WHEN 'FAILED' THEN 'TARGET_FAILED'
                WHEN 'CANCELLED' THEN 'CANCELLED'
            END,
            completed_at = workflow.completed_at
        FROM workflow_flow_runs AS workflow
        WHERE run.target_kind = 'WORKFLOW'
          AND run.target_run_id = workflow.id::text
          AND workflow.status IN ('COMPLETED', 'FAILED', 'CANCELLED')
        """
    )
    op.execute(
        """
        UPDATE schedule_runs AS run
        SET status = CASE conversation.status
                WHEN 'COMPLETED' THEN 'COMPLETED'
                WHEN 'FAILED' THEN 'TARGET_FAILED'
                WHEN 'STOPPED' THEN 'CANCELLED'
            END,
            completed_at = conversation.updated_at
        FROM agent_conversations AS conversation
        WHERE run.target_kind = 'AGENT'
          AND run.target_run_id = conversation.id::text
          AND conversation.status IN ('COMPLETED', 'FAILED', 'STOPPED')
        """
    )
    op.execute(
        """
        UPDATE schedule_runs
        SET completed_at = NULL
        WHERE status = 'DISPATCHED'
        """
    )

    # The schedule ledger is now the sole correlation source. Remove the
    # schedule-specific values without deleting the generic conversation fields.
    op.execute(
        """
        UPDATE agent_conversations AS conversation
        SET origin_type = NULL,
            origin_id = NULL
        FROM schedule_runs AS run
        WHERE run.target_kind = 'AGENT'
          AND run.target_run_id = conversation.id::text
          AND conversation.origin_type = 'SCHEDULE_RUN'
        """
    )

    # Reconstruct the trailing failure streak in target completion order. A
    # completed/cancelled target is the reset boundary; delivery dead letters and
    # terminal target failures both count.
    op.execute(
        """
        WITH latest_reset AS (
            SELECT DISTINCT ON (schedule_id)
                schedule_id,
                completed_at,
                id
            FROM schedule_runs
            WHERE status IN ('COMPLETED', 'CANCELLED')
              AND completed_at IS NOT NULL
            ORDER BY schedule_id, completed_at DESC, id DESC
        ), failure_counts AS (
            SELECT
                schedule.id AS schedule_id,
                COUNT(run.id)::integer AS failure_count
            FROM schedules AS schedule
            LEFT JOIN latest_reset AS reset ON reset.schedule_id = schedule.id
            LEFT JOIN schedule_runs AS run
              ON run.schedule_id = schedule.id
             AND run.status IN ('TARGET_FAILED', 'DEAD_LETTERED')
             AND run.completed_at IS NOT NULL
             AND (
                 reset.schedule_id IS NULL
                 OR (run.completed_at, run.id) > (reset.completed_at, reset.id)
             )
            GROUP BY schedule.id
        )
        UPDATE schedules AS schedule
        SET consecutive_failures = failure_counts.failure_count
        FROM failure_counts
        WHERE failure_counts.schedule_id = schedule.id
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE agent_conversations AS conversation
        SET origin_type = 'SCHEDULE_RUN',
            origin_id = run.id
        FROM schedule_runs AS run
        WHERE run.target_kind = 'AGENT'
          AND run.target_run_id = conversation.id::text
          AND conversation.origin_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE schedule_runs
        SET status = CASE
            WHEN status = 'TARGET_FAILED' THEN 'DEAD_LETTERED'
            WHEN status IN ('COMPLETED', 'CANCELLED') THEN 'DISPATCHED'
            ELSE status
        END
        """
    )
    op.create_index(
        "ix_schedule_runs_retryable_recovery",
        "schedule_runs",
        ["status", "updated_at", "schedule_id"],
        postgresql_where=sa.text("status IN ('RECEIVED', 'PROCESSING', 'FAILED')"),
    )
    op.drop_index("uq_schedule_runs_target", table_name="schedule_runs")
    op.alter_column("schedule_runs", "target_run_id", nullable=True)
    op.drop_index("ix_schedule_runs_user_id", table_name="schedule_runs")
    op.drop_constraint(
        "fk_schedule_runs_user_id_users",
        "schedule_runs",
        type_="foreignkey",
    )
    op.drop_column("schedule_runs", "user_id")
