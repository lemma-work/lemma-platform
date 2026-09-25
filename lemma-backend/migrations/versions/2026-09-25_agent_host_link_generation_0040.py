"""Give each Agent Host a link generation, so one link owns it at a time.

A host's link announces itself on the host's notice channel after ``hello``,
and every other link for the host closes with 4409. Deciding "other" by
connection id meant two handshakes that subscribed before either announced each
heard the other's announcement as the newer one, and both closed.

`link_generation` is what decides instead. Each accepted ``hello`` takes the
next value with one ``UPDATE ... RETURNING`` in the transaction that
authenticates it, so racing handshakes serialize on the row lock and come away
ordered; a link closes only for a generation greater than its own.

A counter rather than a timestamp because two replicas' clocks are not an
order, and `bigint` because it only ever counts up. Existing rows start at 0,
which every first ``hello`` exceeds.

Revision ID: 0040_agent_host_link_generation
Revises: 0039_whatsapp_number_pool
"""

import sqlalchemy as sa
from alembic import op

revision = "0040_agent_host_link_generation"
down_revision = "0039_whatsapp_number_pool"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_hosts",
        sa.Column(
            "link_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_hosts", "link_generation")
