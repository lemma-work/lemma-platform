"""The weeks a pod's measure of what it owns was read in.

A pod can own something -- a bar it holds every week, or a finish line by a
date -- kept on its config with a measure: one read-only query over its tables
that answers a single number. The query answers now and nothing about last
week, so the number is read on a schedule into **`pod_outcome_weeks`**, one row
per pod and week, each reading replacing the week's last. That row is the
history every member of the pod sees; ``measure_digest`` says which measure
answered, so a pod that changes its measure starts a new history.

**`ix_pods_with_measure`** is the sweep's way in: the live pods whose outcome
has a measure, by id, so reading them all costs what those pods cost rather
than a scan of every pod's config.

Nothing existing changes: the table is new and empty, and the index covers no
pod until one is given a measure. The index is built in the migration's own
transaction rather than CONCURRENTLY, as in 0034: CONCURRENTLY cannot run
inside it, and `pods` has one row per pod, read once to find none to index.
The downgrade drops both.

`TABLE` is the table's DDL exactly as the model compiles it, pinned to it by
``datastore/tests/unit/test_outcome_weeks_ddl_matches_models``.

Revision ID: 0066_outcome_weeks
Revises: 0065_schedule_reset_and_author
"""

from alembic import op

revision = "0066_outcome_weeks"
down_revision = "0065_schedule_reset_and_author"
branch_labels = None
depends_on = None

TABLE = """
CREATE TABLE pod_outcome_weeks (
	pod_id UUID NOT NULL,
	starts_on DATE NOT NULL,
	measure_digest VARCHAR(64) NOT NULL,
	value FLOAT,
	target FLOAT,
	problem TEXT,
	measured_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (pod_id, starts_on),
	FOREIGN KEY(pod_id) REFERENCES pods (id) ON DELETE CASCADE
)
"""

INDEX = (
    "CREATE INDEX ix_pods_with_measure ON pods (id) "
    "WHERE (config -> 'outcome' ->> 'measure') IS NOT NULL AND NOT is_deleted"
)


def upgrade() -> None:
    op.execute(TABLE)
    op.execute(INDEX)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_pods_with_measure")
    op.drop_table("pod_outcome_weeks")
