"""Order conversation history by last activity, not by when it began.

The history list was ordered by ``id`` -- time-ordered, so "newest conversation
first". A conversation started last week and answered a minute ago stayed where
it was started, below everything begun since.

`last_activity_at` moves when something outside the agent happens to a
conversation: a person writes, a notification lands in it, a run starts (every
wake starts one) or a run finishes. The agent's own messages -- its text, each
tool call, each tool result -- do not move it; the rule and its reasons are in
``repositories/conversation_activity``. It is its own column rather than
``updated_at`` because ``updated_at`` also moves on a rename or a status repair.

Existing rows take the time their latest run finished (or started, if it is
still going), or their creation time when they have never run. Every message a
person writes either starts a run or steers one that finishes after it, so
that is at least as late as their last message. It reads ``agent_runs``
through ``ix_agent_run_conversation_created`` -- one probe per conversation --
rather than every row of ``agent_messages``. A thread that only ever received
notifications falls back to its creation time.

The two root indexes are rebuilt on ``(last_activity_at, id)`` in place of
``id``, the same expression and predicate otherwise; ``id`` stays as the
tiebreak that makes the keyset cursor total. Built in the migration's own
transaction rather than CONCURRENTLY, for the reason 0025 gives: a failure in
the backfill must not leave a half-applied schema.

Operator note: ``ADD COLUMN`` takes an ACCESS EXCLUSIVE lock that Alembic holds
until the migration commits, so every read and write of conversations waits
out the backfill (measured on Postgres 18: about 5 s for 230k conversations
with 1.1M runs). On an installation where that table is large, run this first,
out of band, while the old code is still serving:

    ALTER TABLE agent_conversations
        ADD COLUMN IF NOT EXISTS last_activity_at timestamptz;

and then the backfill below with ``AND c.id IN (SELECT id FROM
agent_conversations WHERE last_activity_at IS NULL LIMIT 5000)`` appended, in
a loop until it reports zero (about 0.2 s a batch, each committing on its
own, so no lock is held for long). The old code ignores the column. The migration's
own backfill is guarded by ``last_activity_at IS NULL``, so it then only fills
the conversations created in between, and the lock is held for the NOT NULL
check and the two index builds.

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
    # IF NOT EXISTS: the operator note above may already have added it.
    op.execute(
        sa.text(
            "ALTER TABLE agent_conversations "
            "ADD COLUMN IF NOT EXISTS last_activity_at timestamptz"
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE agent_conversations AS c
            SET last_activity_at = COALESCE(
                (SELECT COALESCE(r.finished_at, r.started_at, r.created_at)
                   FROM agent_runs AS r
                  WHERE r.conversation_id = c.id
                  ORDER BY r.created_at DESC
                  LIMIT 1),
                c.created_at
            )
            WHERE c.last_activity_at IS NULL
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
