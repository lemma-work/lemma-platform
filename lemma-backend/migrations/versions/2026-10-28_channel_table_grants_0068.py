"""Tables a group's people outside the pod may read, or add rows to.

People outside the pod in a group read only what the pod made Public and could
write nothing -- so a group where they were meant to record things (a shared
trip's spends, a supplier's delivery dates) could not work. A member can now
grant one group one table: the columns everybody in the group may read, and
the columns they may fill in a new row. Reads and rows go through that member's
own access, and the grant goes with the member, the table or the group.

Expand-only: a new table, nothing backfilled.

Revision ID: 0068_channel_table_grants
Revises: 0067_contact_became_member
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0068_channel_table_grants"
down_revision = "0067_contact_became_member"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datastore_channel_grants",
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
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("read_columns", postgresql.JSONB(), nullable=False),
        sa.Column("add_columns", postgresql.JSONB(), nullable=False),
        sa.Column(
            "granted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_datastore_channel_grants_table_channel",
        "datastore_channel_grants",
        ["table_id", "channel_id"],
        unique=True,
    )
    op.create_index(
        "ix_datastore_channel_grants_channel",
        "datastore_channel_grants",
        ["channel_id"],
    )
    op.create_index(
        "ix_datastore_channel_grants_granted_by",
        "datastore_channel_grants",
        ["granted_by"],
    )


def downgrade() -> None:
    op.drop_table("datastore_channel_grants")
