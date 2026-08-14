"""Give timer rows a fire lease, so a failed dispatch is retried and not lost.

Schedules can be claimed with a cursor: advancing ``next_fire_at`` in the
claiming transaction is durable, and the next occurrence is a different one.
Timers have no next occurrence -- a `WAIT_UNTIL` or a snooze fires once -- so
there is nothing to advance, and a row lock alone is not a claim: it is released
at commit, and the next tick would pick the same row up again.

A lease closes that. Claiming stamps ``fire_lease_until``; the due query skips
rows whose lease is still live. A dispatch that succeeds ends with the wait no
longer ACTIVE, so it leaves the due set for good. A dispatch that fails -- or a
replica that dies holding the lease -- simply lets the lease expire, and another
replica retries. That is the same shape ``DomainEventOutbox`` already uses, and
the reason to copy it rather than invent something is that its failure modes are
already understood here.

Both columns are nullable: a NULL lease means "never claimed", which is what
every existing row is.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_timer_fire_leases"
down_revision = "0019_scheduler_due_cursors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("workflow_run_waits", "agent_conversation_waits"):
        op.add_column(
            table,
            sa.Column("fire_lease_until", sa.DateTime(timezone=True), nullable=True),
        )

    # The due indexes from 0019 lead with scheduled_at; the lease is a filter
    # applied after it, so they still serve the query. Replacing them with
    # composites would only help if leases were common, and a live lease means
    # a fire is in flight -- which is rare by construction.


def downgrade() -> None:
    for table in ("workflow_run_waits", "agent_conversation_waits"):
        op.drop_column(table, "fire_lease_until")
