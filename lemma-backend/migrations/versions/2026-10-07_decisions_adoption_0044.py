"""Schedules and workflows asking decisions: what the database needs.

An event a schedule's filter skips is now recorded as a FILTERED run, so a busy
webhook schedule's ledger can be mostly skips. Two reads must not walk past
them:

- the circuit breaker's failure streak, whose index was partial on
  ``completed_at IS NOT NULL`` only -- the new one leaves skips out too, and
  replaces the old one, which served this query alone;
- a schedule's run history without its skips (``skipped=false``), which the
  run list now offers and the app shows by default.

Built and dropped concurrently: ``schedule_runs`` is written on every fire.

Revision ID: 0044_decisions_adoption
Revises: 0043_surface_groups
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0044_decisions_adoption"
down_revision = "0043_surface_groups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_schedule_runs_schedule_streak",
            "schedule_runs",
            ["schedule_id", sa.text("completed_at DESC"), sa.text("id DESC")],
            unique=False,
            postgresql_where=sa.text(
                "completed_at IS NOT NULL AND status <> 'FILTERED'"
            ),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_schedule_runs_schedule_fires",
            "schedule_runs",
            ["schedule_id", sa.text("created_at DESC"), sa.text("id DESC")],
            unique=False,
            postgresql_where=sa.text("status <> 'FILTERED'"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index(
            "ix_schedule_runs_schedule_completed",
            table_name="schedule_runs",
            postgresql_concurrently=True,
            if_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_schedule_runs_schedule_fires",
            table_name="schedule_runs",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.create_index(
            "ix_schedule_runs_schedule_completed",
            "schedule_runs",
            ["schedule_id", sa.text("completed_at DESC"), sa.text("id DESC")],
            unique=False,
            postgresql_where=sa.text("completed_at IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index(
            "ix_schedule_runs_schedule_streak",
            table_name="schedule_runs",
            postgresql_concurrently=True,
            if_exists=True,
        )
