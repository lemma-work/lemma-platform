"""Make the cached/uncached split and the cost's provenance first-class.

``usage_records`` recorded ``input_tokens`` as a single number that silently
included cache reads, and priced them correctly — the discount was applied, it
just left no trace. So a run that was 90% cached and one that was not looked
identical in the API and cost ten times less, and nobody could explain the
difference from the data. The counts existed only inside the ``metadata`` JSONB
blob, which a grouped aggregate cannot sum without hydrating every row on the
highest-insert-rate table in the system.

``cost_source`` is the other half. Cost is now resolved in layers — a registered
rate first, then the public ``genai-prices`` dataset — so a runtime profile
somebody added with their own key finally reports what it spent instead of a
permanent null. A best-effort number that cannot be told apart from an
authoritative one is worse than no number, so the row says which layer answered.

Three deliberate choices:

**No index.** Migration 0018 dropped twelve single-column indexes here for the
reason that applies to every one of them: this table gains a row per model call
and an index is paid on each. Nothing filters on a token count.

**No table rewrite.** All three columns are ``NOT NULL`` with a constant
``server_default``, which PostgreSQL 11+ records as catalog metadata rather than
rewriting every existing row. On this table that is the difference between a
migration and an outage.

**No backfill.** Historical rows read zero cached tokens and ``UNKNOWN``
provenance. Both are honest: their ``metadata`` still carries whatever
``cache_read_tokens`` was observed at the time, their ``cost_usd`` was computed
with that discount already applied, and "this row predates provenance tracking"
is exactly what ``UNKNOWN`` means. Rewriting ~every row of the busiest table to
restate what the blob already says is not worth the lock.

``agent_runs.usage_reservation`` rides along for a related leak. A run reserves
against its spend counters before it starts and releases at the end, but the
handle lived only in the worker's memory -- so a SIGKILLed worker stranded that
reservation until the whole window rolled over, permanently shrinking that
person's allowance in the meantime. ``reconcile_orphaned_agent_runs`` is the
process that cleans up after a dead worker and could not do it here, because it
had nothing to release. Nullable, so this one is metadata-only too.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0030_usage_cost_breakdown"
# Re-parented onto main's head when this branch merged: #346 landed
# 0021_app_release_history and 0022_function_revisions as children of
# 0029_github_app_reauth, which is where this one also hung. Two heads is
# not a conflict alembic can resolve, and the chain is linear by policy.
down_revision = "0022_function_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usage_records",
        sa.Column(
            "cached_input_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "usage_records",
        sa.Column(
            "cache_write_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "usage_records",
        sa.Column(
            "cost_source",
            sa.String(length=20),
            nullable=False,
            server_default="UNKNOWN",
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column("usage_reservation", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "usage_reservation")
    op.drop_column("usage_records", "cost_source")
    op.drop_column("usage_records", "cache_write_tokens")
    op.drop_column("usage_records", "cached_input_tokens")
