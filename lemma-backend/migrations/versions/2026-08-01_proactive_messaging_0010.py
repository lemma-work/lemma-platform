"""Add proactive messaging: member reaches, the in-app inbox, and inbound recency.

Three changes that ship together because none of them is useful alone.

``member_reaches`` makes "how can this pod reach this person" a row instead of a
three-way join across the cached platform identity, the conversation link, and
the ``last_event`` blob inside it. It is pod-scoped on purpose;
``agent_surface_external_users`` is correctly cross-pod (one Telegram account,
many pods) and conflating the two is how a shared bot cross-posts between
organizations.

``notifications`` is the in-app inbox — the one reach that cannot 403, expire,
or be muted out of existence, which is what makes it both the fallback for a
failed chat delivery and the durable record of what was sent.

``agent_surface_conversation_links.last_inbound_at`` is the small one that
matters most. The DM reset rule reads ``updated_at``; once a proactive send
bumps that row the 24h reset silently stops firing and yesterday's context leaks
into today. Backfilling it from ``updated_at`` is correct by definition, because
until this revision only inbound events wrote that row.

Delivery receipts are deliberately not here. Fan-out and retry want a row per
attempt, but a single-reach delivery does not, and shipping the table before the
fan-out exists would be schema written against a guess.

Revision ID: 0010_proactive_messaging
Revises: 0009_agent_host
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0010_proactive_messaging"
down_revision = "0009_agent_host"
branch_labels = None
depends_on = None


UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "member_reaches",
        *_audit_columns(),
        sa.Column("pod_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        # NULL for the APP reach, which has no surface behind it.
        sa.Column("surface_id", UUID, nullable=True),
        sa.Column("external_user_id", sa.String(length=255), nullable=True),
        sa.Column("target", JSONB, nullable=True),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default="ACTIVE"
        ),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["pod_id"], ["pods.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["surface_id"], ["agent_surfaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_member_reaches_pod_id", "member_reaches", ["pod_id"])
    op.create_index("ix_member_reaches_user_id", "member_reaches", ["user_id"])
    op.create_index("ix_member_reaches_kind", "member_reaches", ["kind"])
    op.create_index("ix_member_reaches_surface_id", "member_reaches", ["surface_id"])
    op.create_index(
        "ix_member_reach_pod_user_status",
        "member_reaches",
        ["pod_id", "user_id", "status"],
    )
    # One reach per person per channel per pod. Two partial indexes rather than
    # one constraint: PostgreSQL treats NULLs as distinct, so a single unique
    # index over (pod, user, kind, surface_id) would happily admit duplicate APP
    # rows, whose surface_id is always NULL.
    op.create_index(
        "uq_member_reach_pod_user_kind_surface",
        "member_reaches",
        ["pod_id", "user_id", "kind", "surface_id"],
        unique=True,
        postgresql_where=sa.text("surface_id IS NOT NULL"),
    )
    op.create_index(
        "uq_member_reach_pod_user_kind_app",
        "member_reaches",
        ["pod_id", "user_id", "kind"],
        unique=True,
        postgresql_where=sa.text("surface_id IS NULL"),
    )

    op.create_table(
        "notifications",
        *_audit_columns(),
        sa.Column("pod_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("conversation_id", UUID, nullable=True),
        sa.Column("agent_id", UUID, nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("origin_type", sa.String(length=32), nullable=True),
        sa.Column("origin_id", UUID, nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["pod_id"], ["pods.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["agent_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_pod_id", "notifications", ["pod_id"])
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index(
        "ix_notifications_conversation_id", "notifications", ["conversation_id"]
    )
    op.create_index("ix_notifications_agent_id", "notifications", ["agent_id"])
    op.create_index(
        "ix_notification_pod_user_created",
        "notifications",
        ["pod_id", "user_id", "created_at"],
    )
    # The badge query, and the only one on the render hot path.
    op.create_index(
        "ix_notification_user_unread",
        "notifications",
        ["user_id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )

    op.add_column(
        "agent_surface_conversation_links",
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Correct by definition: until this revision, only inbound events wrote this
    # row, so updated_at *was* the last inbound time.
    op.execute(
        "UPDATE agent_surface_conversation_links SET last_inbound_at = updated_at"
    )


def downgrade() -> None:
    op.drop_column("agent_surface_conversation_links", "last_inbound_at")

    op.drop_index("ix_notification_user_unread", table_name="notifications")
    op.drop_index("ix_notification_pod_user_created", table_name="notifications")
    op.drop_index("ix_notifications_agent_id", table_name="notifications")
    op.drop_index("ix_notifications_conversation_id", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_index("ix_notifications_pod_id", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index("uq_member_reach_pod_user_kind_app", table_name="member_reaches")
    op.drop_index("uq_member_reach_pod_user_kind_surface", table_name="member_reaches")
    op.drop_index("ix_member_reach_pod_user_status", table_name="member_reaches")
    op.drop_index("ix_member_reaches_surface_id", table_name="member_reaches")
    op.drop_index("ix_member_reaches_kind", table_name="member_reaches")
    op.drop_index("ix_member_reaches_user_id", table_name="member_reaches")
    op.drop_index("ix_member_reaches_pod_id", table_name="member_reaches")
    op.drop_table("member_reaches")
