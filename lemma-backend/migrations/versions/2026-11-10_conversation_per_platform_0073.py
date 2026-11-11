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

The column is committed on its own before the index swap, so the code of this
release -- which may start before the migration ends -- finds it at once. The
old index is dropped before the new one is built, so that code can open a
conversation on a second platform at once; the per-person advisory lock keeps
opens from doubling up meanwhile. Every step reruns cleanly. Downgrade
archives all but the most recently active of a person's conversations in a
channel before narrowing the index again.

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


def _swap_in(columns: str, *, first: tuple[str, ...] = ()) -> None:
    """Drop the unique index in place, then build its replacement.

    Dropped first, not after: while both stand, the old one-per-person index
    refuses the conversation this release opens on a second platform, and
    every existing conversation is on none -- so every person's first message
    on every platform would fail until the swap. Between the drop and the
    build, `lock_person_in_channel` still serializes each person's opens, so
    nothing doubles up. Concurrently: every inbound message reads and writes
    this table. ``first`` run before, in the same block: entering it commits
    what came before, so a column added just before is there for the code that
    reads it while the build is still going.
    """
    with op.get_context().autocommit_block():
        for statement in first:
            op.execute(statement)
        _repair_invalid(_BUILDING)
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {_BUILDING} "
            f"ON agent_conversations {columns} WHERE {_LIVE}"
        )
        op.execute(f"ALTER INDEX {_BUILDING} RENAME TO {_INDEX}")


def upgrade() -> None:
    # IF NOT EXISTS: the autocommit block below commits the column, and a
    # failed build leaves the revision unstamped, so a rerun meets it again.
    op.execute(
        "ALTER TABLE agent_conversations ADD COLUMN IF NOT EXISTS platform VARCHAR(32)"
    )
    # Committed on its own before the build: the code of this release reads
    # and writes the column, and may start before the migration ends.
    _swap_in(_PER_PLATFORM)


def downgrade() -> None:
    _swap_in(_PER_PERSON, first=(_ARCHIVE_ALL_BUT_LATEST,))
    op.execute("ALTER TABLE agent_conversations DROP COLUMN IF EXISTS platform")
