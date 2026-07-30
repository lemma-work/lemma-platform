"""fence lifecycle generations

Revision ID: 6bb3a72395fb
Revises: 24278caff64b
Create Date: 2026-07-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


__all__ = (
    "branch_labels",
    "depends_on",
    "down_revision",
    "downgrade",
    "revision",
    "upgrade",
)

revision: str = "6bb3a72395fb"
down_revision: Union[str, Sequence[str], None] = "24278caff64b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Expand-only rollout: the new manager no longer reads or writes the legacy
    # runtime tables, but the previous revision still does. Dropping them before
    # the image switch would break the running revision because migrations are
    # applied before Container Apps rolls forward. A later contract migration
    # removes the ignored tables and their obsolete historical rows after this
    # revision has baked in production.
    inspector = sa.inspect(op.get_bind())
    for table_name in ("sandboxes", "allocations"):
        columns = {
            str(column["name"]) for column in inspector.get_columns(table_name)
        }
        if "resource_generation" not in columns:
            op.add_column(
                table_name,
                sa.Column(
                    "resource_generation",
                    sa.BigInteger(),
                    nullable=False,
                    server_default="1",
                ),
            )


def downgrade() -> None:
    op.drop_column("allocations", "resource_generation")
    op.drop_column("sandboxes", "resource_generation")
