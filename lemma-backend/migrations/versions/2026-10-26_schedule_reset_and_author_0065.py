"""Remember when a schedule's failure streak was reset, and which agent made it.

`schedules.failures_reset_at` is when a person last reactivated the schedule.
The breaker counts its streak back from the run ledger, and reactivating only
zeroed the counter: the next failure recounted the failures from before the
pause and switched the schedule straight off again. The streak now stops at
this instant.

`schedules.created_by_workload_id` is the agent that created the schedule --
a named agent's id, or the pod's id for its default assistant -- and null when
a person did. That agent, acting for the schedule's owner, may delete it again
with the authority it created it with, rather than asking for the destructive
`schedule.delete` approval to clean up after itself.

Both start null, which is what every existing schedule means: no reset since
the ledger began, and no agent author on record. Nothing is rewritten.

The downgrade drops both. Code from before this revision ignores them.

Revision ID: 0065_schedule_reset_and_author
Revises: 0064_grants_every_install
"""

import sqlalchemy as sa
from alembic import op

revision = "0065_schedule_reset_and_author"
down_revision = "0064_grants_every_install"
branch_labels = None
depends_on = None

TABLE = "schedules"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        TABLE,
        sa.Column("failures_reset_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column("created_by_workload_id", sa.Uuid(), nullable=True),
    )
    op.execute("SET LOCAL lock_timeout TO DEFAULT")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_column(TABLE, "created_by_workload_id")
    op.drop_column(TABLE, "failures_reset_at")
    op.execute("SET LOCAL lock_timeout TO DEFAULT")
