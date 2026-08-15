"""Give every timer a due-time the database can index, claim and lease.

One migration for the whole scheduler replacement, because it is one change:
APScheduler is deleted and the rows become the timers. Splitting it into three
would only describe the order I happened to write the code in, and none of it
has shipped, so there is nothing to preserve by keeping the seams.

Three things happen here.

**Cursors.** ``schedules.next_fire_at`` is when a schedule fires next. The
poller claims on it with ``FOR UPDATE SKIP LOCKED`` and advances it in the same
transaction, which is what makes a claim durable and lets N replicas share the
work without a leader.

**Due-times for one-shot timers.** ``workflow_run_waits.scheduled_at`` is
promoted out of JSONB and backfilled from the payload the executor has been
writing all along, so in-flight waits are claimable the moment the poller
starts. ``agent_conversation_waits`` already had the column and only needed the
index.

**Leases.** A schedule can be claimed by advancing its cursor; there is always a
next occurrence. A one-shot timer has none, so a row lock is not a claim -- it
is released at commit and the next tick picks the same row up again.
``fire_lease_until`` closes that: claiming stamps it, the due query skips rows
whose lease is live, and a failed dispatch simply lets the lease expire so
another replica retries. Same shape as ``DomainEventOutbox``, deliberately:
its failure modes are already understood here.

The indexes are partial. Only active rows are ever polled, and that is a small
slice -- an unfiltered index would carry every one-shot that has already fired
and been deactivated, forever. The lease is a filter applied after the due-time,
so the same indexes serve the claim query; a live lease means a fire is in
flight, which is rare by construction.

Also drops ``apscheduler_jobs``. The library created and owned that table
outside ``metadata``, which is why ``env.py`` had to exclude it by name from
autogenerate. Nothing maps or reads it now. Safe under rollback, too: the old
scheduler rebuilt its entire job set from ``schedules`` on every boot -- that is
what ``reconcile_time_schedule_jobs`` did -- so the store was a cache of state
``schedules`` already held. Old code coming back recreates it empty and refills
it on the first reconcile.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_scheduler_postgres_timers"
down_revision = "0018_index_pruning_and_partials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- schedules: the polling cursor ------------------------------------
    op.add_column(
        "schedules",
        sa.Column("next_fire_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_schedules_due
            ON schedules (next_fire_at)
            WHERE schedule_type = 'TIME' AND is_active IS TRUE
        """
    )

    # --- workflow waits: due-time out of JSONB, plus a lease ---------------
    op.add_column(
        "workflow_run_waits",
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
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

    # --- agent snoozes: the column already existed, the index did not ------
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_agent_conversation_waits_due
            ON agent_conversation_waits (scheduled_at)
            WHERE status = 'ACTIVE'
        """
    )

    # --- both wait tables: the fire lease ----------------------------------
    # Nullable because NULL means "never claimed", which every existing row is.
    for table in ("workflow_run_waits", "agent_conversation_waits"):
        op.add_column(
            table,
            sa.Column("fire_lease_until", sa.DateTime(timezone=True), nullable=True),
        )

    # --- the job store nothing owns any more -------------------------------
    op.execute("DROP TABLE IF EXISTS apscheduler_jobs")


def downgrade() -> None:
    # `apscheduler_jobs` is deliberately not recreated: the library owned that
    # schema and built it on startup, so guessing at its columns here would be
    # inventing a table nothing reads. A rollback that reintroduces APScheduler
    # recreates it itself.
    for table in ("workflow_run_waits", "agent_conversation_waits"):
        op.drop_column(table, "fire_lease_until")

    op.execute("DROP INDEX IF EXISTS ix_agent_conversation_waits_due")
    op.execute("DROP INDEX IF EXISTS ix_workflow_run_waits_due")
    op.drop_column("workflow_run_waits", "scheduled_at")
    op.execute("DROP INDEX IF EXISTS ix_schedules_due")
    op.drop_column("schedules", "next_fire_at")
