"""Grants that wait for the resource they name.

`pending_resource_grants` is new. A grant row names its resource by id, so a
bundle import could not write a function's grant on an optional connector the
importing organization had never installed, and dropped it: connecting the
connector later left the function refused. Such a grant now waits here, keyed
by what will identify the resource once it exists (for a connector, the
catalog connector and the mode), and becomes an ordinary grant when it does.

Only adds a table; nothing existing is read or rewritten. Grants dropped by
imports before this release are not recovered: nothing recorded them.

Revision ID: 0077_pending_resource_grants
Revises: 0076_reports_and_blocks
Create Date: 2026-11-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0077_pending_resource_grants"
down_revision = "0076_reports_and_blocks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_resource_grants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "pod_id",
            sa.Uuid(),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("resource_type", sa.String(120), nullable=False),
        sa.Column("resource_key", sa.String(255), nullable=False),
        sa.Column("grantee_type", sa.String(40), nullable=False),
        sa.Column("grantee_id", sa.Uuid(), nullable=False),
        sa.Column(
            "permission_id",
            sa.String(),
            sa.ForeignKey("auth_permissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "pod_id",
            "resource_type",
            "resource_key",
            "grantee_type",
            "grantee_id",
            "permission_id",
            name="uq_pending_resource_grants_scope",
        ),
    )
    op.create_index(
        "ix_pending_resource_grants_grantee",
        "pending_resource_grants",
        ["pod_id", "grantee_type", "grantee_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pending_resource_grants_grantee", table_name="pending_resource_grants"
    )
    op.drop_table("pending_resource_grants")
