"""Stop a connector account taking every schedule bound to it on the way out.

``schedules.account_id`` was ``ON DELETE CASCADE``, and ``accounts`` cascades
from ``auth_configs``, and ``schedule_runs`` cascades from ``schedules``. So
removing one connector install deleted every member's account, every webhook
schedule bound to those accounts, and the whole run history of each -- silently,
with no warning, no count and no way back. An admin tidying up a misconfigured
install destroyed automation other people built and never touched.

``SET NULL`` is what the sibling relationship already does:
``agent_surfaces.account_id`` has been ``SET NULL`` all along, and
``schedules.connector_trigger_id`` -- a column on this very table -- is too. The
schedule survives, holding no account, which is a state a person can see and
repair by pointing it at another one. A deleted row is not.

There is deliberately no data migration. Schedules destroyed by the old cascade
are gone; nothing here can bring them back, and inventing rows would be worse
than the gap.

Revision ID: 0027_schedule_account_set_null
Revises: 0026_account_external_ref
"""

from alembic import op

revision = "0027_schedule_account_set_null"
down_revision = "0026_account_external_ref"
branch_labels = None
depends_on = None

_FK = "schedules_account_id_fkey"


def upgrade() -> None:
    op.drop_constraint(_FK, "schedules", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "schedules",
        "accounts",
        ["account_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(_FK, "schedules", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "schedules",
        "accounts",
        ["account_id"],
        ["id"],
        ondelete="CASCADE",
    )
