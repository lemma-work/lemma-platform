"""Record which workload minted a public link, so a live link stays within it.

A public link no longer serves the bytes it was minted against: it serves the
file at its path as it is now, and a shared page carries the images and
stylesheets it embeds. Both are decided at fetch time, by asking whether the
person who shared the link may still read them — which is the person's access,
and only the person's.

An agent acting for somebody holds less than they do (PS-ACCESS-020: the
intersection, never the union). Re-checking a link an agent minted against the
person alone would let the agent's page carry a picture the agent itself was
never granted. So the link remembers the agent, and the fetch-time check is the
same intersection the agent was held to when it minted.

Null means the person alone: a person minting for themselves, the pod's default
agent (which acts as the person), and every link minted before this column
existed. Those last expire within the seven-day ceiling.
"""

import sqlalchemy as sa
from alembic import op

revision = "0044_signed_link_workload"
down_revision = "0043_surface_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "datastore_signed_links",
        sa.Column("minted_by_workload", sa.String(80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("datastore_signed_links", "minted_by_workload")
