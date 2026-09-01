"""Let a conversation hold more than one person.

``agent_conversations.user_id`` was the whole access model: one owner, and
``validate_conversation_access`` compared against it. That is the right model
for a DM and has no answer at all for a second person, which is what this table
adds. See ``docs/design/agent-conversations.md``.

People and agents share the table. A person row is who may read the
conversation; an agent row is the roster an ``@mention`` resolves against and a
router chooses from. Exactly one of the two columns is set per row, and the
check constraint enforces that rather than trusting the three code paths that
create conversations to agree.

The two unique constraints are separate on purpose. Postgres treats NULLs as
distinct in a unique index, so one constraint over both columns would not stop
a person being added twice, while the pair does -- and neither bounds how many
agents a conversation may hold.

Every existing conversation is backfilled with an OWNER row for the user_id it
already had, so membership is complete from the first deploy rather than only
for conversations created after it. Until a run acts as its sender rather than
as its conversation, the access check still accepts the owner column directly;
this backfill is what lets that clause be removed later without locking anybody
out of their own history.
"""

import sqlalchemy as sa
from alembic import op


revision = "0025_conversation_participants"
down_revision = "0024_drop_surface_schedule_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_conversation_participants",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["agent_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "(user_id IS NULL) <> (agent_id IS NULL)",
            name="ck_conversation_participant_exactly_one_subject",
        ),
        sa.UniqueConstraint(
            "conversation_id", "user_id", name="uq_conversation_participant_user"
        ),
        sa.UniqueConstraint(
            "conversation_id", "agent_id", name="uq_conversation_participant_agent"
        ),
    )
    op.create_index(
        "ix_agent_conversation_participants_conversation_id",
        "agent_conversation_participants",
        ["conversation_id"],
    )
    op.create_index(
        "ix_conversation_participant_user",
        "agent_conversation_participants",
        ["user_id", "conversation_id"],
    )
    op.execute(
        """
        INSERT INTO agent_conversation_participants
            (id, created_at, conversation_id, user_id, agent_id, role)
        SELECT
            gen_random_uuid(),
            c.created_at,
            c.id,
            c.user_id,
            NULL,
            'OWNER'
        FROM agent_conversations AS c
        ON CONFLICT ON CONSTRAINT uq_conversation_participant_user DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_participant_user",
        table_name="agent_conversation_participants",
    )
    op.drop_index(
        "ix_agent_conversation_participants_conversation_id",
        table_name="agent_conversation_participants",
    )
    op.drop_table("agent_conversation_participants")
