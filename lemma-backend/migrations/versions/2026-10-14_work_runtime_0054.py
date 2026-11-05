"""Work runtime, and file processing on it.

**The runtime.** ``work_items`` holds live work only -- a completed item is
deleted in the transaction that completes it -- plus ``dead`` items kept for
inspection and retry. Every claim is an index probe on one of the partial
indexes below, so the table's cost tracks pending work, not history.
``lease_epoch`` is created here, with the table, so the claim fence never
needs a column added to a hot table later; nothing in this release reads it.
The agent turns that move onto the runtime later (#981) bump it on every claim
and match it in their fenced statements, so a run whose lease was taken over
cannot write as the new claim.

``work_queues`` has one row per (class, namespace) that has had work: its cap,
its named rates and its place in the round robin. It stores no readiness flag;
whether a namespace has claimable work is derived from ``work_items`` at claim
time. ``work_rate_buckets`` holds the token buckets rate-limited kinds draw
from at claim time; a missing row is a full bucket.

Both queue tables churn: ``work_items`` deletes every completed row and
``work_queues`` rewrites ``last_served`` on every claim round. Autovacuum's
default scale factor would let dead tuples pile up to a fifth of the table
before it looks, and the claim's index probes slow down with them, so both
are vacuumed on a fixed threshold instead.

**Files.** Four columns on ``datastore_files``:

* ``indexed_sha256`` -- the bytes the current chunks and derived artifacts were
  made from. NULL on existing rows, which read it as ``content_sha256``.
* ``storage_layout`` -- ``path`` for every existing row (bytes keyed by path);
  rows written once the object layout is switched on say ``object``.
* ``processing_claim`` -- the processing claim's fencing token, which only
  ever moves on. ``processing_attempts`` keeps the meaning people and clients
  already read (attempts that failed on the current bytes) instead of
  becoming a token that grows across revisions.
* ``parent_id`` -- the folder a row sits in. Filled for new writes; existing
  rows are filled by the ``datastore.parent_backfill`` job, not here, because
  the table is written on every upload and a full-table update would hold it.

And the cutover: processing used to be driven by a stream consumer and two
crons reading ``status``. This release is deployed with the previous one's
workers stopped, and it replays nothing: every file that was waiting or
mid-extraction becomes ``FAILED`` with a note to retry it, and an existing
``FAILED`` row stays as it is. Nothing is processed until someone asks (the
retry API accepts both), so a release cannot set off a backlog of extractions
and embeddings. The runtime fences each run on ``processing_claim``; retry
budgets live on the work item.

Every step is idempotent: the concurrent index builds commit on their own, so
a failure after them leaves the revision half applied, and a rerun must finish
it rather than trip over what is already there.

Revision ID: 0054_work_runtime
Revises: 0053_folder_visibility_index
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0054_work_runtime"
down_revision = "0053_folder_visibility_index"
branch_labels = None
depends_on = None


_WAITING = "kind = 'FILE' AND search_enabled AND status IN ('PENDING', 'PROCESSING')"
_NOT_PROCESSED = (
    "Not processed before the upgrade. Retry processing to index this file."
)


def _repair_invalid(name: str) -> None:
    """Drop a half-built index so `IF NOT EXISTS` stops mistaking it for done.

    An interrupted `CREATE INDEX CONCURRENTLY` leaves the index behind with
    `indisvalid = false`. The name exists, so on the rerun `IF NOT EXISTS`
    skips the build, the revision is stamped, and the planner never uses the
    index -- or, for a unique one, never enforces it. Checking first is what
    makes a rerun recover (the same guard as 0038 and 0053).
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


def seed_statements() -> list[str]:
    """The cutover, as statements -- importable so a test can run them."""
    # Nothing in flight is carried over: a person retries a file to index it.
    return [
        (
            "UPDATE datastore_files SET status = 'FAILED', "
            f"last_processing_error = '{_NOT_PROCESSED}' WHERE {_WAITING}"
        )
    ]


#: Storage parameters for the churning queue tables (see the module docstring).
_VACUUM = {
    "work_items": (
        "autovacuum_vacuum_scale_factor = 0, autovacuum_vacuum_threshold = 5000, "
        "autovacuum_vacuum_cost_delay = 0, autovacuum_analyze_scale_factor = 0.02"
    ),
    "work_queues": (
        "autovacuum_vacuum_scale_factor = 0, autovacuum_vacuum_threshold = 1000, "
        "fillfactor = 70"
    ),
}


def _tune_vacuum() -> None:
    for table, options in _VACUUM.items():
        op.execute(sa.text(f"ALTER TABLE {table} SET ({options})"))


def _add_file_columns() -> None:
    op.add_column(
        "datastore_files",
        sa.Column("indexed_sha256", sa.String(64), nullable=True),
        if_not_exists=True,
    )
    op.add_column(
        "datastore_files",
        sa.Column(
            "storage_layout",
            sa.String(16),
            nullable=False,
            server_default="path",
        ),
        if_not_exists=True,
    )
    op.add_column(
        "datastore_files",
        sa.Column("processing_claim", sa.Integer(), nullable=False, server_default="0"),
        if_not_exists=True,
    )
    op.add_column(
        "datastore_files",
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        if_not_exists=True,
    )


def _create_work_tables() -> None:
    op.create_table(
        "work_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ns", sa.Text(), nullable=False),
        sa.Column("class", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("dedup_key", sa.Text()),
        sa.Column("concurrency_key", sa.Text()),
        sa.Column("concurrency_limit", sa.Integer()),
        sa.Column("rate_key", sa.Text()),
        sa.Column(
            "state", sa.Text(), nullable=False, server_default=sa.text("'queued'")
        ),
        sa.Column(
            "run_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "priority", sa.SmallInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "lease_losses", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("first_unavailable_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.Text()),
        sa.Column(
            "lease_epoch", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("error_class", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        if_not_exists=True,
    )
    op.create_index(
        "work_items_dedup",
        "work_items",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL AND state = 'queued'"),
        if_not_exists=True,
    )
    op.create_index(
        "work_items_ready",
        "work_items",
        ["class", "ns", sa.text("priority DESC"), "run_at"],
        postgresql_where=sa.text("state = 'queued'"),
        if_not_exists=True,
    )
    op.create_index(
        "work_items_running",
        "work_items",
        ["class", "ns"],
        postgresql_where=sa.text("state = 'running'"),
        if_not_exists=True,
    )
    op.create_index(
        "work_items_keyed",
        "work_items",
        ["concurrency_key"],
        postgresql_where=sa.text("state = 'running' AND concurrency_key IS NOT NULL"),
        if_not_exists=True,
    )
    op.create_index(
        "work_items_leases",
        "work_items",
        ["lease_until"],
        postgresql_where=sa.text("state = 'running'"),
        if_not_exists=True,
    )
    op.create_index(
        "work_items_dead",
        "work_items",
        ["run_at"],
        postgresql_where=sa.text("state = 'dead'"),
        if_not_exists=True,
    )

    op.create_table(
        "work_queues",
        sa.Column("class", sa.Text(), nullable=False),
        sa.Column("ns", sa.Text(), nullable=False),
        sa.Column("max_running", sa.Integer(), nullable=False),
        sa.Column(
            "last_served",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("'epoch'::timestamptz"),
        ),
        sa.Column(
            "paused", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "rates",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("class", "ns"),
        if_not_exists=True,
    )
    op.create_index(
        "work_queues_served",
        "work_queues",
        ["class", "last_served"],
        if_not_exists=True,
    )

    op.create_table(
        "work_rate_buckets",
        sa.Column("ns", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("cap", sa.Double(), nullable=False),
        sa.Column("per_second", sa.Double(), nullable=False),
        sa.Column("tokens", sa.Double(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("full_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("ns", "name", "key"),
        if_not_exists=True,
    )
    op.create_index(
        "work_rate_buckets_full",
        "work_rate_buckets",
        ["full_at"],
        if_not_exists=True,
    )


def upgrade() -> None:
    _create_work_tables()
    _tune_vacuum()
    _add_file_columns()
    for statement in seed_statements():
        op.execute(sa.text(statement))
    # Concurrently, outside the transaction: the file table is written on
    # every upload, and a plain build blocks those writers for the scan.
    with op.get_context().autocommit_block():
        _repair_invalid("ix_datastore_file_parent")
        op.create_index(
            "ix_datastore_file_parent",
            "datastore_files",
            ["parent_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        _repair_invalid("ix_datastore_file_parent_missing")
        op.create_index(
            "ix_datastore_file_parent_missing",
            "datastore_files",
            ["pod_id"],
            postgresql_where=sa.text(
                "parent_id IS NULL AND strpos(substr(path, 2), '/') > 0"
            ),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        _repair_invalid("ix_datastore_file_path_layout")
        op.create_index(
            "ix_datastore_file_path_layout",
            "datastore_files",
            ["id"],
            postgresql_where=sa.text("storage_layout = 'path' AND kind = 'FILE'"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


class ObjectLayoutRowsExist(RuntimeError):
    """Raised by the downgrade while any file's bytes are keyed by its id."""


def object_layout_guard() -> str:
    """The count the downgrade refuses on -- importable so a test can run it."""
    return "SELECT count(*) FROM datastore_files WHERE storage_layout = 'object'"


def downgrade() -> None:
    # Irreversible once a file is in the object layout: its bytes live under
    # `pods/{pod}/objects/{id}/...` and only `storage_layout` says so. Dropping
    # the column makes every such file read at its path key -- which the copy
    # released, and which a file written with the switch on never had -- so
    # the files would 404 with nothing left recording where their bytes are.
    # Nothing copies bytes back; refuse rather than lose them.
    moved = op.get_bind().scalar(sa.text(object_layout_guard()))
    if moved:
        raise ObjectLayoutRowsExist(
            f"Refusing to downgrade 0054_work_runtime: {moved} datastore "
            "file(s) are stored in the object layout (storage_layout = 'object'), "
            "and dropping the column would leave their bytes unreachable. There "
            "is no reverse copy; downgrading past this revision is only possible "
            "before DATASTORE_OBJECT_STORAGE_LAYOUT has been switched on."
        )
    with op.get_context().autocommit_block():
        for name in (
            "ix_datastore_file_path_layout",
            "ix_datastore_file_parent_missing",
            "ix_datastore_file_parent",
        ):
            op.drop_index(
                name,
                table_name="datastore_files",
                postgresql_concurrently=True,
                if_exists=True,
            )
    op.drop_column("datastore_files", "parent_id")
    op.drop_column("datastore_files", "processing_claim")
    op.drop_column("datastore_files", "storage_layout")
    op.drop_column("datastore_files", "indexed_sha256")
    op.drop_table("work_rate_buckets")
    op.drop_table("work_queues")
    op.drop_table("work_items")
