"""The run log, and the kind of turn each run is.

`agent_run_steps` is new: the run log, what each model step added to a
conversation's model history, as pydantic-ai serialized it. `messages` is TEXT,
never JSONB, which would re-sort the keys it exists to keep. Nothing is
backfilled: a conversation's first run after this takes its past in from the
message rows once and logs it. `agent_messages.run_step_seq` says which step
carries a row into that history; NULL until one does.

`agent_runs.kind` says who the turn is for. Whatever starts a run sets it from
now on; it is backfilled from the run's conversation's `origin`, except that a
person's message into a workflow's or a schedule's conversation is a person's
turn. A call's coordinator and a channel line have kinds of their own.

Adding a column with a constant default rewrites no row on Postgres 11+; the
backfill touches only runs whose conversation is not a person's chat.

Revision ID: 0070_agent_run_log
Revises: 0069_agent_typed_state
Create Date: 2026-11-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0070_agent_run_log"
down_revision = "0069_agent_typed_state"
branch_labels = None
depends_on = None

_RUN_KINDS = (
    "CHAT",
    "SURFACE",
    "CHANNEL",
    "CONTACT",
    "WORKFLOW",
    "SCHEDULE",
    "SUBAGENT",
    "POD_ASK",
    "CALL",
)
_STEP_KINDS = ("STEP", "COMPACTION", "NOTICE", "RESUME", "HELD", "FOLDED")

#: A run's kind, from its conversation's origin as `run_origin` read it -- but
#: a person's message (`MESSAGE`) into an automation's conversation started a
#: person's turn.
_RUN_KIND_BACKFILL = """
UPDATE agent_runs r SET kind = CASE
  WHEN c.origin IN ('WORKFLOW_STEP', 'SCHEDULE') AND r.trigger = 'MESSAGE'
    THEN 'CHAT'
  WHEN c.origin = 'WORKFLOW_STEP' THEN 'WORKFLOW'
  WHEN c.origin = 'NOTIFICATION' THEN 'SURFACE'
  WHEN c.origin IN ('SCHEDULE', 'SUBAGENT', 'POD_ASK', 'SURFACE', 'CONTACT',
    'CHANNEL', 'CALL') THEN c.origin
  ELSE 'CHAT' END
FROM agent_conversations c
WHERE c.id = r.conversation_id AND c.origin NOT IN ('CHAT', 'PROJECT', 'SETUP')
"""


def _enum(name: str, *values: str, length: int) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "kind",
            _enum("runkind", *_RUN_KINDS, length=16),
            nullable=False,
            server_default="CHAT",
        ),
    )
    op.add_column(
        "agent_messages", sa.Column("run_step_seq", sa.BigInteger(), nullable=True)
    )
    op.get_bind().execute(sa.text(_RUN_KIND_BACKFILL))
    op.create_table(
        "agent_run_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind", _enum("runstepkind", *_STEP_KINDS, length=16), nullable=False
        ),
        sa.Column("messages", sa.Text(), nullable=False),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_agent_run_step_seq"),
    )
    op.create_index(
        "ix_agent_run_steps_conversation_seq",
        "agent_run_steps",
        ["conversation_id", "seq"],
    )


def downgrade() -> None:
    op.drop_table("agent_run_steps")
    op.drop_column("agent_messages", "run_step_seq")
    op.drop_column("agent_runs", "kind")
