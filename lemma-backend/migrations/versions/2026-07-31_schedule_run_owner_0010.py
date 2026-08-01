"""Add rolling-safe schedule run ownership and target outcomes.

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
    op.add_column("schedule_runs", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.add_column(
        "schedule_runs", sa.Column("target_outcome", sa.String(32), nullable=True)
    )
    op.add_column(
        "schedule_runs", sa.Column("redrive_of_run_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "schedule_runs", sa.Column("redriven_by_user_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_schedule_runs_user_id_users",
        "schedule_runs",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_schedule_runs_redrive_of_run_id_schedule_runs",
        "schedule_runs",
        "schedule_runs",
        ["redrive_of_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_schedule_runs_redriven_by_user_id_users",
        "schedule_runs",
        "users",
        ["redriven_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_schedule_runs_user_id", "schedule_runs", ["user_id"])
    op.create_index(
        "uq_schedule_runs_target",
        "schedule_runs",
        ["target_kind", "target_run_id"],
        unique=True,
        postgresql_where=sa.text("target_run_id IS NOT NULL"),
    )
    op.create_index(
        "uq_schedule_runs_redrive",
        "schedule_runs",
        ["redrive_of_run_id"],
        unique=True,
        postgresql_where=sa.text("redrive_of_run_id IS NOT NULL"),
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

    op.execute(
        """
        UPDATE schedule_runs AS run
        SET target_outcome = CASE workflow.status
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
        SET target_outcome = CASE conversation.status
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
        WITH latest_reset AS (
            SELECT DISTINCT ON (schedule_id)
                schedule_id,
                completed_at,
                id
            FROM schedule_runs
            WHERE COALESCE(target_outcome, status) IN ('COMPLETED', 'CANCELLED')
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
             AND (
                 COALESCE(run.target_outcome, run.status)
                    IN ('TARGET_FAILED', 'DEAD_LETTERED')
             )
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

    op.execute(
        """
        UPDATE schedules
        SET is_active = false,
            last_fire_status = 'ERROR',
            last_error = 'DATASTORE schedules must declare an explicit table_name.'
        WHERE schedule_type = 'DATASTORE'
          AND (
              NOT (config ? 'table_name')
              OR NULLIF(BTRIM(config->>'table_name'), '') IS NULL
          )
        """
    )


def downgrade() -> None:
    op.drop_index("uq_schedule_runs_redrive", table_name="schedule_runs")
    op.drop_index("uq_schedule_runs_target", table_name="schedule_runs")
    op.drop_index("ix_schedule_runs_user_id", table_name="schedule_runs")
    op.drop_constraint(
        "fk_schedule_runs_redriven_by_user_id_users",
        "schedule_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_schedule_runs_redrive_of_run_id_schedule_runs",
        "schedule_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_schedule_runs_user_id_users",
        "schedule_runs",
        type_="foreignkey",
    )
    op.drop_column("schedule_runs", "redriven_by_user_id")
    op.drop_column("schedule_runs", "redrive_of_run_id")
    op.drop_column("schedule_runs", "target_outcome")
    op.drop_column("schedule_runs", "user_id")
