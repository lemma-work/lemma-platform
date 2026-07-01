"""agent_surfaces: add stable, pod-unique name

Adds ``name`` — the identifier the REST API now addresses surfaces by (like
agent names), replacing platform-keyed paths now that a pod may have several
surfaces of the same platform. Existing rows are backfilled to the lowercased
platform, with a numeric suffix on any collisions.

Revision ID: 0003_agent_surfaces_name
Revises: 0002_accounts_multiple
Create Date: 2026-07-01

"""

import warnings
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["downgrade", "upgrade", "schema_upgrades", "schema_downgrades", "data_upgrades", "data_downgrades"]

# revision identifiers, used by Alembic.
revision = '0003_agent_surfaces_name'
down_revision = '0002_accounts_multiple'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        with op.get_context().autocommit_block():
            schema_upgrades()
            data_upgrades()


def downgrade() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        with op.get_context().autocommit_block():
            data_downgrades()
            schema_downgrades()


def schema_upgrades() -> None:
    op.add_column(
        'agent_surfaces',
        sa.Column('name', sa.String(length=255), nullable=True),
    )
    op.execute("UPDATE agent_surfaces SET name = lower(surface_type)")
    # Dedupe any (pod_id, name) collisions left by the backfill (e.g. a pod that
    # already had multiple surfaces of the same platform): keep the oldest row's
    # name, suffix the rest.
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY pod_id, name ORDER BY created_at, id
            ) AS rn
            FROM agent_surfaces
        )
        UPDATE agent_surfaces a
        SET name = a.name || '-' || ranked.rn
        FROM ranked
        WHERE a.id = ranked.id AND ranked.rn > 1
        """
    )
    op.alter_column('agent_surfaces', 'name', nullable=False)
    op.create_index('ix_agent_surfaces_name', 'agent_surfaces', ['name'])
    op.create_unique_constraint(
        'uq_agent_surface_pod_name', 'agent_surfaces', ['pod_id', 'name']
    )


def schema_downgrades() -> None:
    op.drop_constraint('uq_agent_surface_pod_name', 'agent_surfaces', type_='unique')
    op.drop_index('ix_agent_surfaces_name', table_name='agent_surfaces')
    op.drop_column('agent_surfaces', 'name')


def data_upgrades() -> None:
    pass


def data_downgrades() -> None:
    pass
