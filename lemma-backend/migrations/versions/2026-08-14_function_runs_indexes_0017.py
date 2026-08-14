"""Index the columns the function-run cron actually filters on.

``function_runs`` carried exactly one index: ``ix_function_runs_id``, on the
primary key, which the primary key already provides. Every query that matters
read the table sequentially.

Two of them run once a minute, forever, from ``reconcile_function_runs``.
``fail_expired`` filters on ``status`` and ``deadline_at`` and orders by
``deadline_at, id``; ``list_pending_async_runs`` filters on ``status``,
``job_id`` and ``deadline_at`` and orders by ``created_at, id``. Neither had
anything to read but the heap, and the table has no retention -- so the scan
got longer every day and the sort with it. Both indexes below are partial: the
predicates exclude terminal runs, which are almost the whole table and can
never match either query, and the sort columns lead so the LIMIT is answered
from the index rather than by sorting the matches.

The third index is the foreign key. Postgres does not index a referencing
column automatically, so ``list_runs_by_function`` scanned the table, and so
did the ``ON DELETE CASCADE`` from ``functions`` on every function deletion.

Dropping ``ix_function_runs_id`` costs nothing: the primary key constraint has
its own unique index on the same column, and this one was pure duplicate write
amplification on a table whose write path is the hot one.

Creating these on a large table takes an ACCESS EXCLUSIVE lock for the duration
of the build. They are created non-concurrently on purpose -- CONCURRENTLY
cannot run inside Alembic's migration transaction, and the cron that needs them
tolerates a short stall far better than it tolerates the sequential scans.

Revision ID: 0017_function_runs_indexes
Revises: 0016_unique_surface_email
"""

from alembic import op


revision = "0017_function_runs_indexes"
down_revision = "0016_unique_surface_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX ix_function_runs_function_id
        ON function_runs (function_id, id DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_function_runs_expiring
        ON function_runs (deadline_at, id)
        WHERE deadline_at IS NOT NULL AND status IN ('PENDING', 'RUNNING')
        """
    )
    op.execute(
        """
        CREATE INDEX ix_function_runs_pending_async
        ON function_runs (created_at, id)
        WHERE status = 'PENDING'
          AND job_id IS NOT NULL
          AND deadline_at IS NOT NULL
        """
    )
    # Redundant with the primary key's own unique index.
    op.execute("DROP INDEX IF EXISTS ix_function_runs_id")


def downgrade() -> None:
    op.execute("CREATE INDEX ix_function_runs_id ON function_runs (id)")
    op.execute("DROP INDEX IF EXISTS ix_function_runs_pending_async")
    op.execute("DROP INDEX IF EXISTS ix_function_runs_expiring")
    op.execute("DROP INDEX IF EXISTS ix_function_runs_function_id")
