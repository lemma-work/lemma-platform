"""Tables open to people outside the pod: who may add rows, and which columns.

**`datastore_public_rows`** is one grant per table: rows may be added from
outside the pod -- by its confirmed contacts, or by anyone -- with only the
listed columns, as the member who opened it. A form is any page that adds such
a row; the table, not the page, decides what it may write. The grant goes with
its table and with the member who opened it, so either being deleted closes the
table to outside rows. Nothing existing is open, so nothing changes.
``opened_by`` is indexed because deleting a member cascades through it.

**`datastore_tables.contact_columns`** is what a contact may read of their own
rows of a contact-owned table: a member's explicit list, never every column.

**`schedules.include_outside_rows`** lets a DATASTORE schedule fire on rows
people outside the pod added. Off by default: a stranger's words starting a
pod's agent is something a member opts into, schedule by schedule.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045_public_rows"
down_revision = "0044_contacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datastore_public_rows",
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
        sa.Column(
            "opened_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_datastore_public_rows_table",
        "datastore_public_rows",
        ["table_id"],
        unique=True,
    )
    op.create_index("ix_datastore_public_rows_pod", "datastore_public_rows", ["pod_id"])
    op.create_index(
        "ix_datastore_public_rows_opened_by", "datastore_public_rows", ["opened_by"]
    )
    op.add_column(
        "datastore_tables",
        sa.Column("contact_columns", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "schedules",
        sa.Column(
            "include_outside_rows",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("schedules", "include_outside_rows")
    op.drop_column("datastore_tables", "contact_columns")
    op.drop_index(
        "ix_datastore_public_rows_opened_by", table_name="datastore_public_rows"
    )
    op.drop_index("ix_datastore_public_rows_pod", table_name="datastore_public_rows")
    op.drop_index("uq_datastore_public_rows_table", table_name="datastore_public_rows")
    op.drop_table("datastore_public_rows")
