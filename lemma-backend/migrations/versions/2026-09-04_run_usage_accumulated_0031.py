"""Let a run's spend survive the worker that was spending it.

A run's usage lived in the worker's memory until the run finished, and was
written once at the end. That is fine when runs end. They do not always: a
SIGTERM parks the run for another worker to reclaim, a SIGKILL leaves nothing
behind at all, and in both cases the tokens the abandoned attempt had already
bought were billed to nobody -- against `PS-OPS-003`, which says a run is
recorded "however the run ended".

``usage_accumulated`` is where that spend goes as it happens, keyed by attempt:

    {"<attempt id>": {"input_tokens": 4000, "output_tokens": 120, ...}}

Keyed rather than summed because a reclaimed run is the *same* run under a new
attempt, so a flat total would have to be read before it could be added to, and
two workers briefly overlapping would lose one of their halves. A per-attempt
key makes each write absolute and therefore idempotent: the same attempt
writing twice says the same thing, and no attempt can overwrite another's.

The finalizer sums the attempts into the single ``usage_records`` row and clears
the column; ``reconcile_orphaned_agent_runs`` does the same for a run whose
worker never came back. A non-null value on a terminal run means neither has
happened yet.

Nullable JSONB with no default and no index: this is metadata-only, so
PostgreSQL records it in the catalog rather than rewriting the table, and
nothing filters on it -- the reconciler finds its rows by status and age.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031_run_usage_accumulated"
down_revision = "0030_usage_cost_breakdown"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("usage_accumulated", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "usage_accumulated")
