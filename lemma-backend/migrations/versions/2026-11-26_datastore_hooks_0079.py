"""Hooks: an address a service outside the pod posts to, one row per delivery.

**`datastore_hooks`** is new: one row per hook -- its table, how a body
becomes a row (`fields`, null for the whole body), the SHA-256 of the secret
its address carries, whether it is on, the member its rows are added as, and
its last delivery and last refusal. It goes with its pod, its table and the
member who made it. `created_by` is indexed because deleting a member
cascades through it; `table_id` because deleting a table does.

A table rather than a key in `datastore_tables.config`: that JSON is written
whole by anyone who can change the table and returned to anyone who can read
it, a delivery has to find its hook by id with no session, and every delivery
writes the hook's last-received time -- which on the table's row would move
the pod's inventory on every form submission. Nothing exists yet, so nothing
changes.

Revision ID: 0079_datastore_hooks
Revises: 0078_pending_resource_grants
Create Date: 2026-11-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0079_datastore_hooks"
down_revision = "0078_pending_resource_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datastore_hooks",
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
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("fields", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_datastore_hooks_pod_name",
        "datastore_hooks",
        ["pod_id", "name"],
        unique=True,
    )
    op.create_index("ix_datastore_hooks_table", "datastore_hooks", ["table_id"])
    op.create_index("ix_datastore_hooks_created_by", "datastore_hooks", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_datastore_hooks_created_by", table_name="datastore_hooks")
    op.drop_index("ix_datastore_hooks_table", table_name="datastore_hooks")
    op.drop_index("uq_datastore_hooks_pod_name", table_name="datastore_hooks")
    op.drop_table("datastore_hooks")
