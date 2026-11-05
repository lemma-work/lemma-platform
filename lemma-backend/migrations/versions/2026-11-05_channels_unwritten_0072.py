"""Two channel columns stop being required, so the code can stop writing them.

Nothing reads ``channel_cases.opened_seq`` or ``channel_push_pending.to_seq``.
The code that ships with this still writes both, because it must also run
against the schema before it, where ``opened_seq`` is NOT NULL and the push
window's check ties ``to_seq`` to ``from_seq``. Once this is deployed, a later
release stops writing them and drops them.

Expand-only: ``opened_seq`` loses NOT NULL (a catalog change), and the window
check is replaced by the same check without ``to_seq``, which every row
already meets. The new check is added ``NOT VALID`` and validated after, so
the table is locked exclusively only while the constraint is swapped, not
while it is scanned.

The downgrade fills what the old schema requires -- a case's ``opened_seq``
as the channel's newest line before it opened, a waiting window's ``to_seq``
as its ``lead_seq`` -- and puts the old constraints back.

``CHANGED`` is each change as the channels DDL test reads it: the table, the
text of migration 0058's statement for it, and what this migration makes it --
pinned to the models by ``channels/tests/unit/test_channels_ddl_matches_models``.

Revision ID: 0072_channels_unwritten
Revises: 0071_asked_at_source
"""

from alembic import op

revision = "0072_channels_unwritten"
down_revision = "0071_asked_at_source"
branch_labels = None
depends_on = None

_WINDOW = "ck_channel_push_pending_window"
_OLD_WINDOW = (
    "(lines = 0) = (from_seq IS NULL) AND (from_seq IS NULL) = (to_seq IS NULL)"
    " AND (to_seq IS NULL) = (lead_seq IS NULL)"
    " AND (lead_seq IS NULL) = (next_attempt_at IS NULL)"
)
_NEW_WINDOW = (
    "(lines = 0) = (from_seq IS NULL) AND (from_seq IS NULL) = (lead_seq IS NULL)"
    " AND (lead_seq IS NULL) = (next_attempt_at IS NULL)"
)

CHANGED = (
    ("channel_cases", "opened_seq BIGINT NOT NULL", "opened_seq BIGINT"),
    (
        "channel_push_pending",
        f"CONSTRAINT {_WINDOW} CHECK ({_OLD_WINDOW})",
        f"CONSTRAINT {_WINDOW} CHECK ({_NEW_WINDOW})",
    ),
)


def upgrade() -> None:
    op.execute("ALTER TABLE channel_cases ALTER COLUMN opened_seq DROP NOT NULL")
    op.execute(
        f"ALTER TABLE channel_push_pending DROP CONSTRAINT {_WINDOW}, "
        f"ADD CONSTRAINT {_WINDOW} CHECK ({_NEW_WINDOW}) NOT VALID"
    )
    op.execute(f"ALTER TABLE channel_push_pending VALIDATE CONSTRAINT {_WINDOW}")


def downgrade() -> None:
    op.execute(
        """
        UPDATE channel_cases AS c
        SET opened_seq = coalesce(
            (
                SELECT max(line.seq) FROM channel_messages AS line
                WHERE line.channel_id = c.channel_id
                  AND line.created_at < c.opened_at
            ),
            0
        )
        WHERE c.opened_seq IS NULL
        """
    )
    op.execute("ALTER TABLE channel_cases ALTER COLUMN opened_seq SET NOT NULL")
    op.execute(
        "UPDATE channel_push_pending SET to_seq = lead_seq"
        " WHERE (to_seq IS NULL) <> (from_seq IS NULL)"
    )
    op.execute(
        f"ALTER TABLE channel_push_pending DROP CONSTRAINT {_WINDOW}, "
        f"ADD CONSTRAINT {_WINDOW} CHECK ({_OLD_WINDOW}) NOT VALID"
    )
    op.execute(f"ALTER TABLE channel_push_pending VALIDATE CONSTRAINT {_WINDOW}")
