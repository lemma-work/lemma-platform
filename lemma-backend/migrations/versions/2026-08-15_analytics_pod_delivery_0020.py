"""Remember which pods have already delivered, so activation fires once.

``pod.delivered`` is the activation metric: the first time a pod produced an
outcome for somebody other than the person building it, or produced one
autonomously. It must fire exactly once per pod, forever, and the projection
that raises it runs in a worker that can be restarted, redeployed and scaled.

The marker row is that memory. Postgres rather than Redis because a cache flush
would re-fire activation for every established pod at once and corrupt the funnel
irreversibly; Redis sits in front of it as a negative cache, which is safe
because a miss is only ever a wasted read.

The backfill is the other half. Every pod that has already produced a completed
agent run, workflow run or schedule run had activated long before this table
existed. Marking them ``seeded`` records that without emitting anything: dating
their activation to the deploy would invent a platform-wide spike on a day
nothing happened, and leaving them unmarked would do worse -- the next run of an
established pod would report it as newly activated.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_analytics_pod_delivery"
down_revision = "0019_scheduler_postgres_timers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analytics_pod_delivery",
        sa.Column("pod_id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("via", sa.String(length=32), nullable=True),
        sa.Column(
            "seeded", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )

    # Seed every pod that has already delivered. `ON CONFLICT DO NOTHING` so the
    # three sources can overlap freely -- a pod that has done all three is still
    # one row, and which source found it first does not matter for a cohort we
    # are deliberately not attributing.
    #
    # Each source reaches pod_id differently, which is the same asymmetry the
    # runtime projection has to deal with: an agent run is scoped to a
    # conversation and a schedule run to a schedule, and only a workflow run
    # carries the pod directly.
    op.execute(
        """
        INSERT INTO analytics_pod_delivery (pod_id, delivered_at, via, seeded)
        SELECT DISTINCT c.pod_id, NULL, NULL, true
        FROM agent_runs AS r
        JOIN agent_conversations AS c ON c.id = r.conversation_id
        WHERE c.pod_id IS NOT NULL
          AND upper(r.status::text) IN ('COMPLETED', 'SUCCEEDED')
        ON CONFLICT (pod_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO analytics_pod_delivery (pod_id, delivered_at, via, seeded)
        SELECT DISTINCT r.pod_id, NULL, NULL, true
        FROM workflow_flow_runs AS r
        WHERE r.pod_id IS NOT NULL
          AND upper(r.status::text) IN ('COMPLETED', 'SUCCEEDED')
        ON CONFLICT (pod_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO analytics_pod_delivery (pod_id, delivered_at, via, seeded)
        SELECT DISTINCT s.pod_id, NULL, NULL, true
        FROM schedule_runs AS r
        JOIN schedules AS s ON s.id = r.schedule_id
        WHERE s.pod_id IS NOT NULL
          AND upper(r.status::text) IN ('COMPLETED', 'SUCCEEDED')
        ON CONFLICT (pod_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("analytics_pod_delivery")
