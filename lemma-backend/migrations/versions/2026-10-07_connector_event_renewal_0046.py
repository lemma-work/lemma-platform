"""When each MCP event subscription is next renewed, and how often it failed.

The refresher used to take the subscriptions soonest to lapse and filter them
for due ones. A renewal that failed left its row in place, already lapsed and
so first in line, and once a batch's worth of those piled up nothing else was
ever selected: every healthy subscription lapsed at its server.

**`renew_after`** is when the refresher next tries a row: halfway through its
grant on success, and after a failure a retry that waits longer each time.
The refresher selects only rows whose `renew_after` has passed, oldest first,
so a row the server keeps refusing moves to the back. **`renew_failures`**
counts the failures in a row; a grant resets it.

Granted rows are backfilled with the time the refresher would have renewed
them: halfway through the grant, or two five-minute passes before it lapses
if that is sooner. Pending rows have no grant yet and keep `renew_after` empty.

Revision ID: 0046_connector_event_renewal
Revises: 0045_connector_events
"""

import sqlalchemy as sa
from alembic import op

revision = "0046_connector_event_renewal"
down_revision = "0045_connector_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connector_event_subscriptions",
        sa.Column("renew_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "connector_event_subscriptions",
        sa.Column("renew_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE connector_event_subscriptions SET renew_after = LEAST("
        "granted_at + (refresh_before - granted_at) / 2, "
        "refresh_before - interval '10 minutes') "
        "WHERE granted_at IS NOT NULL AND refresh_before IS NOT NULL"
    )
    op.drop_index(
        "ix_connector_event_subscriptions_refresh",
        table_name="connector_event_subscriptions",
    )
    op.create_index(
        "ix_connector_event_subscriptions_renew",
        "connector_event_subscriptions",
        ["renew_after"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_connector_event_subscriptions_renew",
        table_name="connector_event_subscriptions",
    )
    op.create_index(
        "ix_connector_event_subscriptions_refresh",
        "connector_event_subscriptions",
        ["refresh_before"],
    )
    op.drop_column("connector_event_subscriptions", "renew_failures")
    op.drop_column("connector_event_subscriptions", "renew_after")
