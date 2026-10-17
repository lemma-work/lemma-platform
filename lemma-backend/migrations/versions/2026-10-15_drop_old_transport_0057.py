"""Drop the old transport and the columns only the previous release read.

The contract step after ``0056_agent_surface_bundle_work``, which was
expand-only. Running it ends the rollback window to an image older than 0056.

* ``agent_conversation_waits.fire_lease_until`` and ``.wake_attempts``: the
  waits fire through ``agent.wait.fire`` / ``agent.wait.poll`` items now.
* ``schedule_runs.last_inspected_at`` and ``ix_schedule_runs_recoverable``
  (dropped concurrently, so the table is never locked for writes): an agent or
  workflow outcome reaches its schedule run as a fact written in the run's
  finishing transaction, and the recovery sweep that read them is gone.
* ``domain_event_outbox`` and ``domain_event_inbox``, whatever they still
  hold: the cutover in ``0054``-``0056`` replays nothing from them. Like that
  release, this one is deployed with the previous release's workers stopped.
  A separately configured datastore database keeps an unused outbox of its
  own; nothing reads it, and an operator may drop it by hand. The old Redis
  streams and consumer groups are likewise unread, and expire with Redis.

Every step is idempotent: the concurrent index drop commits on its own, so a
failure after it leaves the revision half applied, and a rerun must finish it.

The downgrade restores the columns and the index, and recreates the two
tables, empty, as 0003 created them.

Revision ID: 0057_drop_old_transport
Revises: 0056_agent_surface_bundle_work
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0057_drop_old_transport"
down_revision = "0056_agent_surface_bundle_work"
branch_labels = None
depends_on = None

_TABLES = ("domain_event_inbox", "domain_event_outbox")


def _exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT to_regclass(:name) IS NOT NULL"), {"name": name})
        .scalar()
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_schedule_runs_recoverable",
            table_name="schedule_runs",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.execute(
        sa.text("ALTER TABLE schedule_runs DROP COLUMN IF EXISTS last_inspected_at")
    )
    op.execute(
        sa.text(
            "ALTER TABLE agent_conversation_waits "
            "DROP COLUMN IF EXISTS fire_lease_until, "
            "DROP COLUMN IF EXISTS wake_attempts"
        )
    )
    for table in _TABLES:
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))


def downgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE agent_conversation_waits "
            "ADD COLUMN IF NOT EXISTS fire_lease_until timestamptz, "
            "ADD COLUMN IF NOT EXISTS wake_attempts integer NOT NULL DEFAULT 0"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE schedule_runs "
            "ADD COLUMN IF NOT EXISTS last_inspected_at timestamptz"
        )
    )
    op.create_index(
        "ix_schedule_runs_recoverable",
        "schedule_runs",
        [sa.text("last_inspected_at ASC NULLS FIRST"), "id"],
        postgresql_where=sa.text("target_outcome IS NULL"),
        if_not_exists=True,
    )
    if not _exists("domain_event_outbox"):
        _create_outbox()
    if not _exists("domain_event_inbox"):
        _create_inbox()


def _create_outbox() -> None:
    op.create_table(
        "domain_event_outbox",
        sa.Column("stream", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("producer", sa.String(length=120), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("causation_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(length=160), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_type", sa.String(length=200), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_domain_event_outbox_ready",
        "domain_event_outbox",
        ["available_at", "occurred_at", "id"],
        postgresql_where=sa.text("published_at IS NULL AND dead_lettered_at IS NULL"),
    )
    op.create_index(
        "ix_domain_event_outbox_expired_lease",
        "domain_event_outbox",
        ["lease_until", "occurred_at", "id"],
        postgresql_where=sa.text(
            "lease_until IS NOT NULL AND published_at IS NULL "
            "AND dead_lettered_at IS NULL"
        ),
    )
    op.create_index(
        "ix_domain_event_outbox_published_retention",
        "domain_event_outbox",
        ["published_at"],
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )
    op.create_index(
        "ix_domain_event_outbox_dlq_listing",
        "domain_event_outbox",
        [sa.text("dead_lettered_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("dead_lettered_at IS NOT NULL"),
    )
    op.create_index(
        "ix_domain_event_outbox_occurred",
        "domain_event_outbox",
        [sa.text("occurred_at DESC"), sa.text("id DESC")],
    )


def _create_inbox() -> None:
    op.create_table(
        "domain_event_inbox",
        sa.Column("consumer", sa.String(length=200), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="PROCESSING", nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("delivery_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("first_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_type", sa.String(length=200), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "consumer", "event_id", name="uq_domain_event_inbox_consumer_event"
        ),
    )
    op.create_index(
        "ix_domain_event_inbox_status_received",
        "domain_event_inbox",
        ["status", "last_received_at"],
    )
    op.create_index(
        "ix_domain_event_inbox_completed_retention",
        "domain_event_inbox",
        ["completed_at"],
        postgresql_where=sa.text("completed_at IS NOT NULL"),
    )
    op.create_index(
        "ix_domain_event_inbox_dlq_retention",
        "domain_event_inbox",
        ["dead_lettered_at"],
        postgresql_where=sa.text("dead_lettered_at IS NOT NULL"),
    )
