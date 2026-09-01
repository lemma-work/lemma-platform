"""Record which person wrote a message.

``role`` said a human spoke; nothing said which one. That was answerable by
inference while a conversation had exactly one person in it -- every user
message was the owner's -- and stops being answerable the moment a second
person is in one. See ``docs/design/agent-conversations.md``.

Nullable with no backfill, deliberately. Every existing user message belongs to
the conversation's owner, so a backfill would be derivable rather than
informative, and it would write the owner's id onto surface messages that came
from somebody who has no account here at all. NULL reads as "before we
recorded this", which is true, and the owner column still answers for those
rows.

``ON DELETE SET NULL`` rather than CASCADE: a message is part of the
conversation's record and does not stop existing because its author's account
did.
"""

import sqlalchemy as sa
from alembic import op


revision = "0026_message_sender"
down_revision = "0025_conversation_participants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_messages",
        sa.Column("sender_user_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_messages_sender_user_id",
        "agent_messages",
        "users",
        ["sender_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agent_messages_sender_user_id", "agent_messages", type_="foreignkey"
    )
    op.drop_column("agent_messages", "sender_user_id")
