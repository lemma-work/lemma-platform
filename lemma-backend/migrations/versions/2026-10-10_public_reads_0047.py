"""Tables people outside the pod may read: which columns, ordered how.

**`datastore_public_reads`** is one grant per table: its rows may be read from
outside the pod -- by its confirmed contacts, or by anyone -- with only the
listed columns, in ascending order of ``order_by``, as the member who opened
it. A booking page reading free slots is the first; the table, not the page,
decides what it shows. The grant goes with its table and with the member who
opened it. Nothing existing is open, so nothing changes. ``opened_by`` is
indexed because deleting a member cascades through it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0047_public_reads"
down_revision = "0046_decisions_adoption"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datastore_public_reads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "pod_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "table_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datastore_tables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("audience", sa.String(20), nullable=False),
        sa.Column("columns", postgresql.JSONB(), nullable=False),
        sa.Column("order_by", sa.String(255), nullable=True),
        sa.Column(
            "opened_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_datastore_public_reads_table",
        "datastore_public_reads",
        ["table_id"],
        unique=True,
    )
    op.create_index(
        "ix_datastore_public_reads_pod", "datastore_public_reads", ["pod_id"]
    )
    op.create_index(
        "ix_datastore_public_reads_opened_by", "datastore_public_reads", ["opened_by"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_datastore_public_reads_opened_by", table_name="datastore_public_reads"
    )
    op.drop_index("ix_datastore_public_reads_pod", table_name="datastore_public_reads")
    op.drop_index(
        "uq_datastore_public_reads_table", table_name="datastore_public_reads"
    )
    op.drop_table("datastore_public_reads")
