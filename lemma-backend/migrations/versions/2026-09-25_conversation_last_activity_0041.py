"""Order conversation history by last activity, not by when it began.

The history list was ordered by ``id`` -- time-ordered, so "newest conversation
first". A conversation started last week and answered a minute ago stayed where
it was started, below everything begun since.

`last_activity_at` is stamped by `append_message`, which every writer in the
module goes through with the row already locked. It is its own column rather
than ``updated_at`` because ``updated_at`` moves on any write -- a rename, a
status change, archiving -- and none of those is somebody talking.

Existing rows take their latest message's time, or their creation time when
they have none. The two root indexes are rebuilt on ``(last_activity_at, id)``
in place of ``id``, the same expression and predicate otherwise; ``id`` stays
as the tiebreak that makes the keyset cursor total.

Built in the migration's own transaction rather than CONCURRENTLY, for the
reason 0025 gives: a failure in the backfill must not leave a half-applied
schema.

Revision ID: 0041_conversation_last_activity
Revises: 0040_agent_host_link_generation
"""

import sqlalchemy as sa
from alembic import op

revision = "0041_conversation_last_activity"
down_revision = "0040_agent_host_link_generation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_conversations",
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE agent_conversations AS c
            SET last_activity_at = COALESCE(
                (SELECT max(m.created_at) FROM agent_messages AS m
                 WHERE m.conversation_id = c.id),
                c.created_at
            )
            """
        )
    )
    op.alter_column(
        "agent_conversations",
        "last_activity_at",
        nullable=False,
        server_default=sa.text("now()"),
    )

    op.create_index(
        "ix_agent_conv_user_pod_roots_activity",
        "agent_conversations",
        ["user_id", "pod_id", "last_activity_at", "id"],
        unique=False,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_index(
        "ix_agent_conv_user_pod_agent_roots_activity",
        "agent_conversations",
        [
            "user_id",
            "pod_id",
            sa.text("COALESCE(agent_id, pod_id)"),
            "last_activity_at",
            "id",
        ],
        unique=False,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.drop_index("ix_agent_conv_user_pod_roots", table_name="agent_conversations")
    op.drop_index(
        "ix_agent_conv_user_pod_agent_roots_v2", table_name="agent_conversations"
    )


def downgrade() -> None:
    op.create_index(
        "ix_agent_conv_user_pod_roots",
        "agent_conversations",
        ["user_id", "pod_id", "id"],
        unique=False,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_index(
        "ix_agent_conv_user_pod_agent_roots_v2",
        "agent_conversations",
        ["user_id", "pod_id", sa.text("COALESCE(agent_id, pod_id)"), "id"],
        unique=False,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.drop_index(
        "ix_agent_conv_user_pod_agent_roots_activity",
        table_name="agent_conversations",
    )
    op.drop_index(
        "ix_agent_conv_user_pod_roots_activity", table_name="agent_conversations"
    )
    op.drop_column("agent_conversations", "last_activity_at")
