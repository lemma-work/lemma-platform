"""Drop ``agent_surfaces.schedule_id``, which nothing can set any more.

The column existed for the polled email surfaces: a Composio trigger fired a
schedule, and the schedule id was how the fire found its way back to the surface
that owned the mailbox. Those surfaces are gone, and with them the only code
that ever wrote this column -- so every remaining row holds NULL and always
will.

This is a schema removal, not a data cleanup. Nothing reads the value, nothing
writes it, and the ``ON DELETE SET NULL`` foreign key to ``schedules`` is the
last thing tying a surface to a table it has no relationship with.

The downgrade restores the column but not its contents, which is the honest
shape: the values were already gone before this ran.
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_drop_surface_schedule_id"
down_revision = "0022_org_names_not_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_agent_surfaces_schedule_id", table_name="agent_surfaces")
    op.drop_column("agent_surfaces", "schedule_id")


def downgrade() -> None:
    op.add_column(
        "agent_surfaces",
        sa.Column("schedule_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "agent_surfaces_schedule_id_fkey",
        "agent_surfaces",
        "schedules",
        ["schedule_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_agent_surfaces_schedule_id", "agent_surfaces", ["schedule_id"])
