"""Record whose message started a run.

A run belonged to its conversation, and the conversation belonged to one
person, so "whose run is this" never had to be asked. It has to be asked the
moment a conversation holds more than one person, for two reasons that arrive
together: the working a run produces is shown only to whoever triggered it, and
the run itself will act with that person's grants rather than the
conversation's. See ``docs/design/agent-conversations.md``.

Backfilled from ``agent_conversations.user_id``. Unlike the message sender
column, this backfill is informative rather than circular: every run that
exists today was started by the only person who could reach the conversation,
so the value is known rather than assumed, and leaving it NULL would make every
historical run's working invisible to the person whose working it is.

``ON DELETE SET NULL``: a run is part of the record and outlives the account
that started it.
"""

import sqlalchemy as sa
from alembic import op


revision = "0027_run_triggered_by"
down_revision = "0026_message_sender"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("triggered_by_user_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_runs_triggered_by_user_id",
        "agent_runs",
        "users",
        ["triggered_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_agent_runs_triggered_by_user_id",
        "agent_runs",
        ["triggered_by_user_id"],
    )
    op.execute(
        """
        UPDATE agent_runs AS r
        SET triggered_by_user_id = c.user_id
        FROM agent_conversations AS c
        WHERE c.id = r.conversation_id
          AND r.triggered_by_user_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_triggered_by_user_id", table_name="agent_runs")
    op.drop_constraint(
        "fk_agent_runs_triggered_by_user_id", "agent_runs", type_="foreignkey"
    )
    op.drop_column("agent_runs", "triggered_by_user_id")
