"""Let a conversation be put away without being destroyed.

A conversation had no ending. The list showed every root conversation a user
had ever started in a pod, newest first, and the only way to make one stop
appearing was to out-scroll it. Delete was the obvious answer and the wrong
one: a conversation owns its messages and runs, a sub-agent conversation
points at its parent with ``ON DELETE SET NULL`` (so deleting a parent
*promotes* its children into the very list you were tidying), and a
surface-bound row is unique on its origin -- delete a Slack thread's
conversation and the next message in that thread silently re-creates an empty
one. Archiving has none of those consequences, because the row stays.

No index. The partial root indexes (``ix_agent_conv_user_pod_roots`` and its
per-agent sibling) already select the rows this filter narrows, and Postgres
drops the archived ones on the way out; a person's archive is a handful of
rows, not a table. A partial index becomes worth its write cost only if the
archived view itself gets slow, which needs an archive far larger than the
history it came from.
"""

import sqlalchemy as sa
from alembic import op


revision = "0023_conversation_archived"
down_revision = "0022_org_names_not_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_conversations",
        sa.Column(
            "is_archived",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_conversations", "is_archived")
