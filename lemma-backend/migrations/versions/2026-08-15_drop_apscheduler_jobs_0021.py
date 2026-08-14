"""Drop the job store. Nothing authoritative ever lived in it.

APScheduler created and owned ``apscheduler_jobs`` itself -- it was never in
``metadata``, which is why ``env.py`` had to exclude it by name from autogenerate
or every migration would have tried to drop it. With the scheduler gone the table
is orphaned: no model maps it, no code reads it.

Dropping it is safe even under a rollback. The old scheduler rebuilt its entire
logical job set from ``schedules`` on every boot -- that is what
``reconcile_time_schedule_jobs`` did -- so the store was a cache of state the
``schedules`` table already held. Old code coming back would recreate the table
empty and refill it on the first reconcile.

``IF EXISTS`` because a database that never ran the old scheduler never had it.
"""

from __future__ import annotations

from alembic import op

revision = "0021_drop_apscheduler_jobs"
down_revision = "0020_timer_fire_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS apscheduler_jobs")


def downgrade() -> None:
    # Deliberately not recreated. The library owned this schema and built it on
    # startup; guessing at its column types here would be inventing a table
    # nothing reads. A rollback that reintroduces APScheduler recreates it.
    pass
