"""Find a pod's workflow runs by time.

An organization's overview counts what each workflow in a pod ran lately: the
pod's runs inside a window (``workflow.contracts.run_activity``). Nothing
indexed runs by pod and time, so that read walked every run the pod ever had
through ``pod_id`` alone, and runs are never pruned.

Nothing else changes. When a member last looked at Home, which the overview
also reads, is kept in their preferences rather than a column.

Revision ID: 0072_workflow_runs_by_time
Revises: 0071_outcome_weeks
"""

import sqlalchemy as sa
from alembic import op

revision = "0072_workflow_runs_by_time"
down_revision = "0071_outcome_weeks"
branch_labels = None
depends_on = None


_INDEX = "ix_workflow_flow_runs_pod_created"


def _repair_invalid(name: str) -> None:
    """Drop a half-built index so `IF NOT EXISTS` stops mistaking it for done.

    An interrupted `CREATE INDEX CONCURRENTLY` leaves the index behind with
    `indisvalid = false`. The name exists, so on the rerun `IF NOT EXISTS`
    skips the build, the revision is stamped, and the planner ignores the
    index for good. Checking first is what makes a rerun recover.
    """
    half_built = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": name},
        )
        .scalar()
    )
    if half_built:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


def upgrade() -> None:
    # Concurrently: every workflow run writes this table, and a plain build
    # would block those writers for the length of the scan.
    with op.get_context().autocommit_block():
        _repair_invalid(_INDEX)
        op.create_index(
            _INDEX,
            "workflow_flow_runs",
            ["pod_id", "created_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX,
            table_name="workflow_flow_runs",
            postgresql_concurrently=True,
            if_exists=True,
        )
