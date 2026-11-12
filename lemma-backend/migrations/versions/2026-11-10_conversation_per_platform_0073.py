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

Existing conversations are not backfilled: each is left with no platform,
so it reads as one kept in Lemma, and a person writing on WhatsApp, email or
any other platform next opens a fresh conversation there. Nothing is deleted;
the old, mixed history stays in Lemma.

The column is added and the old index dropped in one transaction, committed
before the new index is built: the code of this release -- which may start
before the migration ends -- finds the column only once nothing refuses it a
conversation on a second platform. While the new index builds, the per-person
advisory lock keeps opens from doubling up. Every step reruns cleanly.
Downgrade archives all but the most recently active of a person's
conversations in a channel before narrowing the index again.

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


def _build(columns: str) -> None:
    """Build the unique index the old one made way for, and give it its name.

    Concurrently: every inbound message reads and writes this table. Entering
    the autocommit block commits what came before -- the column and the old
    index's drop, together, so the code of this release never meets the one
    without the other.
    """
    with op.get_context().autocommit_block():
        _repair_invalid(_BUILDING)
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {_BUILDING} "
            f"ON agent_conversations {columns} WHERE {_LIVE}"
        )
        op.execute(f"ALTER INDEX {_BUILDING} RENAME TO {_INDEX}")


def upgrade() -> None:
    # IF [NOT] EXISTS: the autocommit block below commits both, and a failed
    # build leaves the revision unstamped, so a rerun meets them again.
    op.execute(
        "ALTER TABLE agent_conversations ADD COLUMN IF NOT EXISTS platform VARCHAR(32)"
    )
    # In the same transaction: while the old one-per-person index stands it
    # refuses the conversation this release opens on a second platform.
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    _build(_PER_PLATFORM)


def downgrade() -> None:
    op.execute(_ARCHIVE_ALL_BUT_LATEST)
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    _build(_PER_PERSON)
    op.execute("ALTER TABLE agent_conversations DROP COLUMN IF EXISTS platform")
