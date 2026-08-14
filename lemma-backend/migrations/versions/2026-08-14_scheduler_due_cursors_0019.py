"""Give every timer a due-time the database can index and claim.

APScheduler kept its own job table and fired from it. Nothing in that table was
authoritative — ``reconcile_time_schedule_jobs`` rebuilt the entire logical job
set from ``schedules`` on every boot, which is the proof. What the job store
actually supplied was a *due-time index* and a claim, and those are the two
things our own tables lacked.

Three columns, one index:

``schedules.next_fire_at`` is the cursor a poller claims. Without it, deciding
what is due means parsing every active schedule's cron on every tick, so the
poll cost grows with the fleet rather than with the work.

``workflow_run_waits.scheduled_at`` promotes the target time out of
``payload->>'scheduled_at'``, where it was unindexable. Note the field already
existed on ``WaitRequest`` and was silently dropped by the repository on the way
in, so the column is less "new state" than "state we were already computing and
throwing away".

``agent_conversation_waits`` already had a typed ``scheduled_at``; what it
lacked was an index for the due query, so the snooze sweep scanned.

All three are nullable and backfilled, so this is safe to apply before the code
that reads them. A row with a NULL cursor is simply not due yet, and the
reconcile pass fills it in.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_scheduler_due_cursors"
down_revision = "0018_index_pruning_and_partials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schedules",
        sa.Column("next_fire_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Partial: only active TIME schedules are ever polled, and that is a small
    # slice of the table. An unfiltered index would carry every one-shot that
    # has already fired and been deactivated, forever.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_schedules_due
            ON schedules (next_fire_at)
            WHERE schedule_type = 'TIME' AND is_active IS TRUE
        """
    )

    op.add_column(
        "workflow_run_waits",
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill from the JSONB the executor has been writing all along, so
    # in-flight waits are claimable the moment the poller starts.
    op.execute(
        """
        UPDATE workflow_run_waits
           SET scheduled_at = (payload ->> 'scheduled_at')::timestamptz
         WHERE scheduled_at IS NULL
           AND payload ? 'scheduled_at'
           AND (payload ->> 'scheduled_at') <> ''
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_workflow_run_waits_due
            ON workflow_run_waits (scheduled_at)
            WHERE status = 'ACTIVE'
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_agent_conversation_waits_due
            ON agent_conversation_waits (scheduled_at)
            WHERE status = 'ACTIVE'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_conversation_waits_due")
    op.execute("DROP INDEX IF EXISTS ix_workflow_run_waits_due")
    op.drop_column("workflow_run_waits", "scheduled_at")
    op.execute("DROP INDEX IF EXISTS ix_schedules_due")
    op.drop_column("schedules", "next_fire_at")
