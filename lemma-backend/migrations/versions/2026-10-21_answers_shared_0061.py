"""A group shows its agents' answers to everybody in it, unless it keeps them
to the asker.

`channels.answers_shared`: when an agent answers a member in a group with
that member's own access, the answer is posted shared (`channel_messages.
shared_at` set as it is written), so the others in the group read it in
full. Off, it is today's rule: read in full by the asker alone, the others
shown that it was given, until the asker shares it. Only a GROUP's is read; a
DIRECT or CONTACT channel keeps the column at its default and ignores it.

Every existing channel takes the default, true: groups that kept answers to
the asker until now show the answers posted from now on to everybody. Lines
already written keep their `shared_at` as it is. Adding a column with a
constant default rewrites no row on Postgres 11+, so the pod's hottest table
is not rewritten.

`COLUMN` is the column's DDL exactly as the model compiles it, pinned to it by
``channels/tests/unit/test_channels_ddl_matches_models``.

It also moves a read number the old ``mentions._give_a_place`` wrote. Somebody
named in a channel they have no seat in was given one at zero, which counted
nothing then: only a seat counted lines. Now a member named where they have no
seat hears of the channel while the mention is unread, from the line that named
them -- so a row left at zero would show that channel's whole history as
unread, "99+" in a busy group. Each such row, a non-member's still at zero with
something naming them there unread, moves to just before the earliest unread
line naming them, with the replies up to there counted read as ``mark_read``
counts them. That is where ``_give_a_place`` now starts somebody named for the
first time. A row already moved by reading, and every seated member's, is left
as it is. The downgrade leaves the rows: under the old rule a non-member's read
number counted nothing, wherever it stood.

Revision ID: 0061_answers_shared
Revises: 0060_slack_apps
"""

from alembic import op

revision = "0061_answers_shared"
down_revision = "0060_slack_apps"
branch_labels = None
depends_on = None

TABLE = "channels"
COLUMN = "answers_shared BOOLEAN DEFAULT true NOT NULL"


#: Every legacy read number of somebody named where they have no seat, moved to
#: the line before the earliest unread line naming them.
MOVE_NAMED_READ_NUMBERS = """
UPDATE channel_read_state AS state
SET last_read_seq = named.first_seq - 1,
    read_replies = (
        SELECT count(*) FROM channel_messages AS line
        WHERE line.channel_id = state.channel_id
          AND line.seq <= named.first_seq - 1
          AND line.thread_root_id IS NOT NULL
    ),
    updated_at = now()
FROM (
    SELECT user_id, channel_id, min(seq) AS first_seq
    FROM channel_mentions
    WHERE user_id IS NOT NULL AND read_at IS NULL
    GROUP BY user_id, channel_id
) AS named
WHERE state.user_id = named.user_id
  AND state.channel_id = named.channel_id
  AND state.last_read_seq = 0
  AND named.first_seq > 1
  AND NOT EXISTS (
      SELECT 1 FROM channel_members AS seat
      WHERE seat.channel_id = state.channel_id AND seat.user_id = state.user_id
  )
"""


def upgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN}")
    op.execute(MOVE_NAMED_READ_NUMBERS)


def downgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN answers_shared")
