"""Schedule triage: a decider's routes on a schedule, and the events it holds.

`schedules.triage` is the triage block a WEBHOOK or DATASTORE schedule carries
instead of a filter: a pod decider and its option -> outcome routes (see
`schedule/domain/triage.py`). `schedules.next_digest_at` is the digest sweep's
cursor, claimed with `FOR UPDATE SKIP LOCKED` the way `next_fire_at` is.

`schedule_runs.held_for` is set exactly while a triaged event is `HELD` --
`digest` or `ask` -- and `digest_run_id` names the one run a digest sent the
event in. Both indexes are partial on what is null for every existing row, so
they start empty.

`schedule_act_admissions` is the events a triage let act within the hour,
one row per event, written under a lock before the event's fire is published:
what `act_per_hour` counts. A new table, so nothing waits on it.

Every column is nullable with no default, so each `ADD COLUMN` is a catalog
change rather than a rewrite. The two index builds read their tables once, in
this transaction rather than CONCURRENTLY, for the reason 0025 gives: a failure
must not leave a half-applied schema.

Revision ID: 0045_schedule_triage
Revises: 0044_decisions
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045_schedule_triage"
down_revision = "0044_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schedules",
        sa.Column("triage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "schedules",
        sa.Column("next_digest_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_schedules_digest_due",
        "schedules",
        ["next_digest_at"],
        unique=False,
        postgresql_where=sa.text("next_digest_at IS NOT NULL"),
    )

    op.add_column(
        "schedule_runs",
        sa.Column("held_for", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "schedule_runs",
        sa.Column("digest_run_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_schedule_runs_held",
        "schedule_runs",
        ["schedule_id", "held_for", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("held_for IS NOT NULL"),
    )

    op.create_table(
        "schedule_act_admissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.Uuid(),
            sa.ForeignKey("schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_event_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "schedule_id", "source_event_id", name="uq_schedule_act_admissions_event"
        ),
    )
    op.create_index(
        "ix_schedule_act_admissions_window",
        "schedule_act_admissions",
        ["schedule_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_schedule_act_admissions_window", table_name="schedule_act_admissions"
    )
    op.drop_table("schedule_act_admissions")
    # The code before this revision has no HELD in either enum and would fail
    # to read a row carrying one. A held event cannot be sent once the triage
    # that holds it is gone, so it ends as skipped.
    op.execute(
        sa.text(
            "UPDATE schedule_runs SET status = 'FILTERED', "
            "target_outcome = 'FILTERED', completed_at = now() "
            "WHERE status = 'HELD'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE schedules SET last_fire_status = 'FILTERED' "
            "WHERE last_fire_status = 'HELD'"
        )
    )
    op.drop_index("ix_schedule_runs_held", table_name="schedule_runs")
    op.drop_column("schedule_runs", "digest_run_id")
    op.drop_column("schedule_runs", "held_for")
    op.drop_index("ix_schedules_digest_due", table_name="schedules")
    op.drop_column("schedules", "next_digest_at")
    op.drop_column("schedules", "triage")
