"""Saved site logins, and a durable record of what is done with them.

``web_logins`` is one person's own way back in to a site Lemma has no connector
for. ``origin`` is plaintext and indexed while ``secret`` is encrypted JSONB, for
the same reason ``accounts.external_ref`` is: choosing which login to inject
means asking "which row is for this origin", and that question has to be
answerable without decrypting every row the person owns.

The unique constraint on ``(user_id, origin)`` makes a second login for the same
site a *replacement* rather than an addition. An agent handed two sessions for
one site has no way to choose between them, and picking the newer one silently is
how a person ends up signed in as somebody they did not mean to be.

Only a *session* is ever stored -- never a password. `connectors-and-accounts.md`
promises the system "shall never ask them for their provider password", and a
password is a different class of secret: reused across sites, and not revocable
without changing it everywhere. `status` is how a session that has stopped
working says so before a run fails on it.

``web_login_sign_in_requests`` is durable rather than a Redis key with an expiry
because the run it belongs to is paused indefinitely. A request that expired
underneath a waiting conversation would leave a person with a dead link and the
agent with nothing to resume from.

``web_login_audit`` is net-new in a broader sense: nothing in this codebase kept
a durable audit trail before, and a credential store is the wrong place to
discover that. Its own table rather than a log line, because the question it
answers — what has been done with my saved logins — has to outlive log retention
and be answerable to the person whose credentials they are. It carries no secret
and no page content.

``web_login_id`` is ``SET NULL`` rather than ``CASCADE``: "it was removed" is
precisely the event somebody would come here to find, so deleting the login must
not delete the record of its deletion.

Revision ID: 0031_saved_site_logins
Revises: 0030_usage_requests
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031_saved_site_logins"
down_revision = "0030_usage_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "web_logins",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("origin", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="ACTIVE"
        ),
        sa.Column("secret", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_hint_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "origin", name="uq_web_logins_user_origin"),
    )
    op.create_index("ix_web_logins_user_id", "web_logins", ["user_id"], unique=False)

    op.create_table(
        "web_login_audit",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("web_login_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("origin", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["web_login_id"], ["web_logins.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_web_login_audit_login", "web_login_audit", ["web_login_id"], unique=False
    )
    op.create_index(
        "ix_web_login_audit_user_created",
        "web_login_audit",
        ["user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "web_login_sign_in_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("origin", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="PENDING"
        ),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("tool_call_id", sa.String(length=255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "saved", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("saved_detail", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_web_login_sign_in_user_created",
        "web_login_sign_in_requests",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_web_login_sign_in_tool_call",
        "web_login_sign_in_requests",
        ["tool_call_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_web_login_sign_in_tool_call", table_name="web_login_sign_in_requests"
    )
    op.drop_index(
        "ix_web_login_sign_in_user_created", table_name="web_login_sign_in_requests"
    )
    op.drop_table("web_login_sign_in_requests")
    op.drop_index("ix_web_login_audit_user_created", table_name="web_login_audit")
    op.drop_index("ix_web_login_audit_login", table_name="web_login_audit")
    op.drop_table("web_login_audit")
    op.drop_index("ix_web_logins_user_id", table_name="web_logins")
    op.drop_table("web_logins")
