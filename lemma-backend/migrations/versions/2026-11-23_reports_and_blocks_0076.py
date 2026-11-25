"""Reports, blocks, and a contact the pod's agents no longer answer.

`content_reports` is new: a person reporting a Lemma's reply, or another
person's line in a shared chat, to whoever runs the deployment. It keeps a
copy of what was reported (`excerpt`), because a chat with a Lemma opens only
for its owner and a line can be edited or removed afterwards. One row per
person per message: reporting it again replaces the row. It goes with the pod
and with the person who reported.

`person_blocks` is new: who a member blocked in one pod's chats -- a member or
a contact, exactly one. Their lines come to that member marked blocked, and
that member is not told of them. It goes with the pod, the member, or the
person blocked.

`contacts.blocked_at` / `blocked_by_user_id`: the pod stopped its agents
answering this contact. Columns rather than a JSON key: contacts have no JSON,
and the turn path reads it before every outsider's run. Both are NULL for
every existing contact, so adding them rewrites no row.

Revision ID: 0076_reports_and_blocks
Revises: 0075_function_assistant_invoke
Create Date: 2026-11-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0076_reports_and_blocks"
down_revision = "0075_function_assistant_invoke"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column(
            "pod_id",
            sa.Uuid(),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reporter_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("place", sa.String(16), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("channel_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("place_title", sa.String(255), nullable=True),
        sa.Column("author_kind", sa.String(16), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("author_name", sa.String(255), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("excerpt", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "reporter_user_id",
            "message_id",
            name="uq_content_reports_reporter_message",
        ),
    )
    op.create_index("ix_content_reports_pod_id", "content_reports", ["pod_id"])
    op.create_index(
        "ix_content_reports_status_created",
        "content_reports",
        ["status", "created_at"],
    )

    op.create_table(
        "person_blocks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "pod_id",
            sa.Uuid(),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "blocked_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "blocked_contact_id",
            sa.Uuid(),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "(blocked_user_id IS NULL) <> (blocked_contact_id IS NULL)",
            name="ck_person_blocks_one_person",
        ),
        sa.UniqueConstraint(
            "user_id", "pod_id", "blocked_user_id", name="uq_person_blocks_member"
        ),
        sa.UniqueConstraint(
            "user_id", "pod_id", "blocked_contact_id", name="uq_person_blocks_contact"
        ),
    )
    op.create_index(
        "ix_person_blocks_pod_member", "person_blocks", ["pod_id", "blocked_user_id"]
    )
    op.create_index(
        "ix_person_blocks_pod_contact",
        "person_blocks",
        ["pod_id", "blocked_contact_id"],
    )

    op.execute(
        "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS blocked_at TIMESTAMPTZ NULL"
    )
    op.execute(
        "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS blocked_by_user_id UUID NULL "
        "CONSTRAINT fk_contacts_blocked_by REFERENCES users (id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.drop_column("contacts", "blocked_by_user_id")
    op.drop_column("contacts", "blocked_at")
    op.drop_table("person_blocks")
    op.drop_table("content_reports")
