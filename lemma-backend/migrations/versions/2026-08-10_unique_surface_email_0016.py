"""One surface per inbound address, enforced by the database.

Inbound mail is routed by ``get_active_by_address``, which matches
``surface_identity_email`` and takes the first row. Nothing stopped two surfaces
holding the same address, and the consequence of that is not an error anybody
would see -- it is one pod quietly receiving another pod's email, with the reply
going back out under the wrong agent's name.

It did not bite while addresses were ``pod-<pod_id.hex>@…``, which cannot
collide by construction. Agent mailboxes are readable (``ops.acme@…``) precisely
so people can use them, and readable means collidable, so the guarantee has to
move into the schema. Allocation now inserts and retries on conflict rather than
checking first, which is only safe with this index present.

Case-insensitive on purpose: mailboxes are not case-sensitive in practice, the
lookup already lowercases, and ``Ops.Acme@`` must not be allocatable alongside
``ops.acme@``. NULL is excluded -- most surfaces are not email and every one of
them has NULL here.

Revision ID: 0016_unique_surface_email
Revises: 0015_apps_public_by_default
"""

from alembic import op


revision = "0016_unique_surface_email"
down_revision = "0015_apps_public_by_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fail loudly rather than dropping mail: if duplicates already exist, this
    # raises and an operator picks which surface keeps the address. Silently
    # rewriting one of them would move somebody's inbound without telling them.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_agent_surface_identity_email
        ON agent_surfaces (lower(surface_identity_email))
        WHERE surface_identity_email IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_agent_surface_identity_email")
