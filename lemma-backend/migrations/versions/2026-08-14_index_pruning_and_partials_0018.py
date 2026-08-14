"""Remove thirteen indexes nothing reads, and point two at their own queries.

An index is not free storage — it is write amplification paid on every insert
and update forever. These were audited against production, and the accounting
comes out at fourteen dropped, one added, two redefined.

``usage_records`` gains a row per model call, the highest insert rate in the
system, and carried twenty-five indexes. Twelve go. Six were strictly redundant:
a btree on ``(a, b)`` already serves a lookup on ``a`` alone, so the
single-column indexes on ``organization_id``, ``pod_id``, ``user_id``,
``agent_id``, ``agent_run_id`` and ``source_type`` duplicated the leading column
of a composite that was already there. ``ix_usage_records_id`` duplicated the
primary key's own unique index. The other five were unusable rather than
duplicated: no query anywhere filters ``conversation_id`` or
``parent_agent_run_id``; ``usage_kind`` is only ever filtered through
``lower()``, which a plain btree cannot serve, and its cardinality is about one;
``model_name`` and ``profile_scope`` are only ever filtered alongside
``organization_id``, where the composite is the better plan regardless.
``ix_usage_records_occurred_at`` stays — the system-cost query may run with no
organization, and then the time range is the only predicate anything can use.

``datastore_files`` is the opposite problem. Its dispatch and recovery sweeps
filter ``status`` with no ``pod_id``, but the only status index led with
``pod_id``; production logged 39,945 sequential scans reading 325,942,950 rows
from a 16,050-row table. The replacement leads with ``status`` and carries the
predicate the sweeps share. That predicate is what makes it cheap: folders are
created NOT_REQUIRED and non-indexable files never enter PENDING, so at rest the
index held zero of those 16,050 rows. A row enters it while it is being ingested
and leaves when it completes. Two redundant indexes on the same table pay for
it — ``pod_id`` already leads three composites, and ``ix_datastore_files_id``
duplicates the primary key.

``schedule_runs`` already had an index for its five-minute recovery sweep. It
was never used once, because ``ix_schedule_runs_retryable_recovery`` covered
RECEIVED/PROCESSING/FAILED while the query asks about PROCESSING/FAILED/
DISPATCHED — Postgres will not use a partial index it cannot prove covers the
query. It was also declared only here and never on the model, so no database
built by ``create_all`` ever had it. The replacement matches the query's status
set and leads with ``(updated_at, id)`` so the ORDER BY comes from the index
instead of a sort.

Created non-concurrently, as in 0017: CONCURRENTLY cannot run inside Alembic's
transaction, and these are all small.

Revision ID: 0018_index_pruning_and_partials
Revises: 0017_function_runs_indexes
"""

from alembic import op


revision = "0018_index_pruning_and_partials"
down_revision = "0017_function_runs_indexes"
branch_labels = None
depends_on = None


# Redundant: each column already leads a composite index on the same table.
_REDUNDANT_USAGE_INDEXES = (
    "ix_usage_records_organization_id",
    "ix_usage_records_pod_id",
    "ix_usage_records_user_id",
    "ix_usage_records_agent_id",
    "ix_usage_records_agent_run_id",
    "ix_usage_records_source_type",
    "ix_usage_records_id",
)

# Unusable: no query filters these, or no btree on them could serve the query
# that does.
_DEAD_USAGE_INDEXES = (
    "ix_usage_records_conversation_id",
    "ix_usage_records_parent_agent_run_id",
    "ix_usage_records_usage_kind",
    "ix_usage_records_model_name",
    "ix_usage_records_profile_scope",
    # No query filters source_id at all -- it appears in entities, schemas and
    # the (source_type, source_id, occurred_at) composite, never in a WHERE.
    "ix_usage_records_source_id",
    # profile_id is filtered, but only ever alongside organization_id, where
    # ix_usage_org_profile_time is the better plan. Zero scans in production.
    "ix_usage_records_profile_id",
)

_USAGE_COLUMNS = {
    "ix_usage_records_organization_id": "organization_id",
    "ix_usage_records_pod_id": "pod_id",
    "ix_usage_records_user_id": "user_id",
    "ix_usage_records_agent_id": "agent_id",
    "ix_usage_records_agent_run_id": "agent_run_id",
    "ix_usage_records_source_type": "source_type",
    "ix_usage_records_id": "id",
    "ix_usage_records_conversation_id": "conversation_id",
    "ix_usage_records_parent_agent_run_id": "parent_agent_run_id",
    "ix_usage_records_usage_kind": "usage_kind",
    "ix_usage_records_model_name": "model_name",
    "ix_usage_records_profile_scope": "profile_scope",
    "ix_usage_records_source_id": "source_id",
    "ix_usage_records_profile_id": "profile_id",
}


def upgrade() -> None:
    for name in _REDUNDANT_USAGE_INDEXES + _DEAD_USAGE_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")

    op.execute("DROP INDEX IF EXISTS ix_datastore_file_status")
    op.execute(
        """
        CREATE INDEX ix_datastore_file_status
        ON datastore_files (status, pod_id, created_at)
        WHERE kind = 'FILE' AND search_enabled
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_datastore_files_pod_id")
    op.execute("DROP INDEX IF EXISTS ix_datastore_files_id")

    op.execute("DROP INDEX IF EXISTS ix_schedule_runs_retryable_recovery")
    op.execute(
        """
        CREATE INDEX ix_schedule_runs_recoverable
        ON schedule_runs (updated_at, id)
        WHERE target_outcome IS NULL
          AND status IN ('PROCESSING', 'FAILED', 'DISPATCHED')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_schedule_runs_recoverable")
    op.execute(
        """
        CREATE INDEX ix_schedule_runs_retryable_recovery
        ON schedule_runs (status, updated_at, schedule_id)
        WHERE status IN ('RECEIVED', 'PROCESSING', 'FAILED')
        """
    )

    op.execute("CREATE INDEX ix_datastore_files_id ON datastore_files (id)")
    op.execute("CREATE INDEX ix_datastore_files_pod_id ON datastore_files (pod_id)")
    op.execute("DROP INDEX IF EXISTS ix_datastore_file_status")
    op.execute(
        "CREATE INDEX ix_datastore_file_status ON datastore_files (pod_id, status)"
    )

    for name in _DEAD_USAGE_INDEXES + _REDUNDANT_USAGE_INDEXES:
        op.execute(
            f"CREATE INDEX {name} ON usage_records ({_USAGE_COLUMNS[name]})"
        )
