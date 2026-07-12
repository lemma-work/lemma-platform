"""Give every schedule run one canonical user owner.

Revision ID: 0006_schedule_run_owner
Revises: 0005_identity_normalization
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision = "0006_schedule_run_owner"
down_revision = "0005_identity_normalization"
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


def downgrade() -> None:
    op.drop_index("ix_schedule_runs_user_id", table_name="schedule_runs")
    op.drop_constraint(
        "fk_schedule_runs_user_id_users",
        "schedule_runs",
        type_="foreignkey",
    )
    op.drop_column("schedule_runs", "user_id")
