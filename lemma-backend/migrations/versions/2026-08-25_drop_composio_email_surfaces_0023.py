"""Remove the Composio-backed Gmail and Outlook surfaces.

An email surface is Resend. Gmail and Outlook were the same idea reached three
different ways -- a polled Composio trigger for inbound, and for outbound three
attachment strategies between them (raw bytes, a Graph draft, and a signed URL
the provider downloads server-side). Reaching a Gmail *account* is still
something an agent does, through the connector; it is no longer a surface.

``agent_surfaces.surface_type`` is a plain string column, so nothing about the
schema changes -- but a row saying ``GMAIL`` no longer parses into
``SurfacePlatform`` and would raise on load. These rows have to go with the code.

**This deletes configuration and cannot be undone by the downgrade.** Check what
is there before running it:

    SELECT pod_id, name, surface_type, is_active
      FROM agent_surfaces
     WHERE surface_type IN ('GMAIL', 'OUTLOOK');

Anyone reached on one of those surfaces should be moved to the pod's Resend
address first; the downgrade restores the ability to *store* such a row, not the
rows themselves.
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_drop_composio_email"
down_revision = "0022_org_names_not_unique"
branch_labels = None
depends_on = None

_GONE = ("GMAIL", "OUTLOOK")


def upgrade() -> None:
    connection = op.get_bind()
    # Links first: they carry the conversation mapping and would otherwise be
    # orphaned rows pointing at a surface that no longer exists.
    connection.execute(
        sa.text(
            "DELETE FROM agent_surface_conversation_links "
            "WHERE surface_id IN ("
            "  SELECT id FROM agent_surfaces WHERE surface_type IN :gone"
            ")"
        ).bindparams(sa.bindparam("gone", value=_GONE, expanding=True))
    )
    connection.execute(
        sa.text("DELETE FROM agent_surfaces WHERE surface_type IN :gone").bindparams(
            sa.bindparam("gone", value=_GONE, expanding=True)
        )
    )


def downgrade() -> None:
    # Nothing to restore: the column is a free-text string, so storing 'GMAIL'
    # was always possible and still is. What the upgrade removed was data, and a
    # downgrade cannot invent it back. Left explicit rather than absent so the
    # asymmetry is a decision on the record.
    pass
