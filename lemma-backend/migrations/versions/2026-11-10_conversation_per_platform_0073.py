"""One conversation per platform: a person's WhatsApp, email and Lemma apart.

A person had one conversation per agent in a channel, and every private place
of theirs -- a WhatsApp chat, an inbox's thread with them, a Slack DM, Lemma
itself -- sits in their one DIRECT channel. So all of it was one history: a
turn asked on WhatsApp read the email exchange too, answered email topics on
WhatsApp, and waited behind a long email run, since a conversation runs one
turn at a time.

A conversation now names the platform it is kept on (``platform``, as its
places name it -- ``WHATSAPP``, ``RESEND``, ``SLACK``, ``WEB``; None for one
kept in Lemma itself), and the unique index that held a person to one
conversation per agent in a channel holds them to one per platform. The
platform, not the place: a chat moved to another pool number, or reached
through another of the pod's bots, is another place and the same chat.

Each live conversation is given the platform it was last spoken on -- the
latest line a person said in it, or before anybody spoke, the latest line it
sent -- so the platform used most recently keeps the history, and any other
opens a conversation of its own when next written on. A line typed in Lemma
has no place, so a conversation last spoken to in Lemma stays Lemma's. One
with no line yet -- a notification nobody has answered -- is on the platform
it was opened on, where the answer will arrive.

The column is committed on its own before the backfill, so the code of this
release -- which may start before the migration ends -- finds it at once. The
code before it runs on unchanged: it continues whichever of a person's
conversations it finds. The new index is built beside the old one and swapped
in, so the table is held to one-per-person throughout. Downgrade archives
all but the most recently active of a person's conversations in a channel
before narrowing the index again.

Revision ID: 0073_conversation_per_platform
Revises: 0072_workflow_runs_by_time
"""

import sqlalchemy as sa
from alembic import op

revision = "0073_conversation_per_platform"
down_revision = "0072_workflow_runs_by_time"
branch_labels = None
depends_on = None


_INDEX = "uq_agent_conversations_channel_person"
_BUILDING = "uq_agent_conversations_channel_person_platform"
_LIVE = "channel_id IS NOT NULL AND parent_id IS NULL AND NOT is_archived"
_PER_PLATFORM = (
    "(channel_id, coalesce(platform, ''), "
    "coalesce(agent_id, pod_id), coalesce(contact_id, user_id))"
)
_PER_PERSON = "(channel_id, coalesce(agent_id, pod_id), coalesce(contact_id, user_id))"

# The platform of the place each conversation was last spoken at, in its own
# channel: the
# latest line a person said (None when typed in Lemma), else the latest line
# it carried anywhere. A place of another channel -- left behind by a merge --
# is no place of this one.
_BACKFILL = """
WITH heard AS (
    SELECT DISTINCT ON (m.conversation_id) m.conversation_id, m.source_id
    FROM channel_messages m
    WHERE m.conversation_id IS NOT NULL AND m.sender_kind <> 'AGENT'
    ORDER BY m.conversation_id, m.created_at DESC, m.seq DESC
),
said AS (
    SELECT DISTINCT ON (m.conversation_id)
        m.conversation_id, coalesce(m.source_id, m.target_source_id) AS source_id
    FROM channel_messages m
    WHERE m.conversation_id IS NOT NULL
    ORDER BY m.conversation_id, m.created_at DESC, m.seq DESC
),
last_place AS (
    SELECT said.conversation_id,
        CASE WHEN heard.conversation_id IS NOT NULL
            THEN heard.source_id ELSE said.source_id END AS source_id
    FROM said LEFT JOIN heard ON heard.conversation_id = said.conversation_id
)
UPDATE agent_conversations c
SET platform = s.platform
FROM last_place p
JOIN channel_sources s ON s.id = p.source_id
WHERE c.id = p.conversation_id
  AND c.channel_id IS NOT NULL
  AND s.channel_id = c.channel_id
"""

# A conversation with no line at all -- a notification opened it, and nobody
# has answered yet -- is on the platform its surface state says it was opened
# on, which is where the answer will be heard.
_BACKFILL_UNSPOKEN = """
UPDATE agent_conversations c
SET platform = st.surface_platform
FROM surface_conversation_state st
WHERE st.conversation_id = c.id
  AND st.surface_platform IS NOT NULL
  AND c.channel_id IS NOT NULL
  AND c.platform IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM channel_messages m WHERE m.conversation_id = c.id
  )
"""

# Downgrade: keep the most recently active of each person's conversations
# with an agent in a channel; archive the rest.
_ARCHIVE_ALL_BUT_LATEST = f"""
UPDATE agent_conversations c SET is_archived = true
FROM (
    SELECT id, row_number() OVER (
        PARTITION BY channel_id, coalesce(agent_id, pod_id),
            coalesce(contact_id, user_id)
        ORDER BY last_activity_at DESC, id DESC
    ) AS rank
    FROM agent_conversations
    WHERE {_LIVE}
) ranked
WHERE c.id = ranked.id AND ranked.rank > 1
"""


def _repair_invalid(name: str) -> None:
    """Drop a half-built index so `IF NOT EXISTS` stops mistaking it for done.

    An interrupted `CREATE INDEX CONCURRENTLY` leaves the index behind with
    `indisvalid = false`; checking first is what makes a rerun recover.
    """
    half_built = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": name},
        )
        .scalar()
    )
    if half_built:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


def _swap_in(columns: str, *, first: tuple[str, ...] = ()) -> None:
    """Build the unique index beside the one in place, then take its name.

    Concurrently: every inbound message reads and writes this table. Built
    under its own name first, so one-per-person holds while it builds.
    ``first`` run before, in the same block: entering it commits what came
    before, so a column added just before is there for the code that reads
    it while the backfill and the build are still going.
    """
    with op.get_context().autocommit_block():
        for statement in first:
            op.execute(statement)
        _repair_invalid(_BUILDING)
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {_BUILDING} "
            f"ON agent_conversations {columns} WHERE {_LIVE}"
        )
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
        op.execute(f"ALTER INDEX {_BUILDING} RENAME TO {_INDEX}")


def upgrade() -> None:
    op.add_column(
        "agent_conversations",
        sa.Column("platform", sa.String(32), nullable=True),
    )
    # Committed on its own before the backfill: the code of this release
    # reads and writes the column, and may start before the migration ends.
    _swap_in(_PER_PLATFORM, first=(_BACKFILL, _BACKFILL_UNSPOKEN))


def downgrade() -> None:
    _swap_in(_PER_PERSON, first=(_ARCHIVE_ALL_BUT_LATEST,))
    op.drop_column("agent_conversations", "platform")
