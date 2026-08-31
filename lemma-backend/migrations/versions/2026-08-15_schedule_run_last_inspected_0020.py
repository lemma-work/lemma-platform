"""Give the recovery sweep a cursor it can actually advance.

``schedule_runs`` had one timestamp doing two jobs. ``updated_at`` recorded when
the row last changed, and the five-minute recovery sweep also used it to decide
which rows to look at next, ordering by it ascending and taking a hundred.

Those are not the same question, and for one branch of the sweep the difference
is fatal. When a run's target is alive but has not finished — a workflow parked
on a human form wait, an agent still running — there is nothing to reconcile.
The handler assigned the row the four values it already held, SQLAlchemy
computed no net change, no UPDATE was emitted, and the ``onupdate`` on
``updated_at`` never fired. So the same hundred rows sorted to the front on the
next tick, and the one after that.

In production this had been stuck for days — the oldest eligible rows all
carried the same timestamp — while the sweep reported a full batch reconciled on
every consecutive sample. Not a full batch: the same batch. The great majority of
eligible rows had never been examined at all, including runs failing a workflow
validation daily and some already dead-lettered.

``last_inspected_at`` separates the two. The sweep stamps it on every row it
reaches, whatever it decides, so a row that legitimately needs no change still
moves the cursor. It also lets re-inspection run on its own cadence — hourly,
rather than every tick — because a target that was still running five minutes
ago is not news, while a *lost* outcome event still wants catching quickly.

The partial index moves with the query it serves, and loses a predicate on the
way. It was ``target_outcome IS NULL AND status IN ('PROCESSING','FAILED',
'DISPATCHED')``; it is now just ``target_outcome IS NULL``. Measured against
production, that status clause excluded a negligible handful of rows — the whole
selectivity was already in ``target_outcome IS NULL``, which by itself keeps the
index to a small fraction of the table. What the clause did carry was the exact
failure mode of 0003: an index predicate that has to stay in step with a query's
status list, and silently stops being used when it does not. A rounding error of
a size saving is not worth a predicate that can disable the index without saying
so.

Null placement is spelled out on both sides. Postgres sorts nulls *last* under
ASC, and an index answers an ORDER BY only when its null placement matches — so
without the explicit NULLS FIRST the planner would read every matching row and
sort them, which is exactly the cost the LIMIT exists to avoid.

The column is deliberately not backfilled. Every existing row starts null, and
null sorting first means the sweep works through the whole untouched backlog
before it returns to anything it has already seen.
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_schedule_run_last_inspected"
down_revision = "0019_scheduler_postgres_timers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schedule_runs",
        sa.Column("last_inspected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_index("ix_schedule_runs_recoverable", table_name="schedule_runs")
    op.create_index(
        "ix_schedule_runs_recoverable",
        "schedule_runs",
        [sa.text("last_inspected_at ASC NULLS FIRST"), "id"],
        postgresql_where=sa.text("target_outcome IS NULL"),
    )
    # The breaker's failure-streak count orders by completed_at, and the only
    # (schedule_id, ...) index led with created_at -- so it could not answer the
    # ordering and Postgres bitmap-scanned the schedule's whole completed
    # history and sorted it, on every run completion. See the repository's
    # `_BREAKER_SCAN_LIMIT` for the other half of that fix.
    op.create_index(
        "ix_schedule_runs_schedule_completed",
        "schedule_runs",
        ["schedule_id", sa.text("completed_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("completed_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_runs_schedule_completed", table_name="schedule_runs")
    op.drop_index("ix_schedule_runs_recoverable", table_name="schedule_runs")
    op.create_index(
        "ix_schedule_runs_recoverable",
        "schedule_runs",
        ["updated_at", "id"],
        postgresql_where=sa.text(
            "target_outcome IS NULL "
            "AND status IN ('PROCESSING', 'FAILED', 'DISPATCHED')"
        ),
    )
    op.drop_column("schedule_runs", "last_inspected_at")
