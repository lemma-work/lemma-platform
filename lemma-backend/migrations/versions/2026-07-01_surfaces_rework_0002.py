"""surfaces rework: multi-account accounts + stable pod-unique surface name

Two changes for the agent-surfaces rework, bundled since neither has shipped
to a live DB yet:

1. accounts: drop the (user_id, auth_config_id) uniqueness so a user can
   connect several accounts to the same app (e.g. multiple Telegram bot
   tokens). Add an ``is_default`` flag (exactly one default per user/auth_config,
   enforced by a partial unique index) used when an account is resolved
   without an explicit id.

2. agent_surfaces: add ``name`` — the stable, pod-unique identifier the REST
   API now addresses surfaces by (like agent names), since a pod may have
   several surfaces of the same platform. Existing rows are backfilled to the
   lowercased platform, with a numeric suffix on any collisions.

Revision ID: 0002_surfaces_rework
Revises: 0001_baseline
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
revision = '0002_surfaces_rework'
down_revision = '0001_baseline'
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
    # --- accounts: multiple per auth config + is_default ---
    op.add_column(
        'accounts',
        sa.Column(
            'is_default',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )
    op.create_index('ix_accounts_is_default', 'accounts', ['is_default'])
    # Existing rows are unique per (user, auth_config) under the old constraint,
    # so promoting them all to default keeps at most one default per pair.
    op.execute("UPDATE accounts SET is_default = true")
    op.drop_index('ix_unique_user_auth_config_account', table_name='accounts')
    op.create_index(
        'uq_accounts_default_per_auth_config',
        'accounts',
        ['user_id', 'auth_config_id'],
        unique=True,
        postgresql_where=sa.text('is_default'),
    )

    # --- agent_surfaces: stable, pod-unique name ---
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

    op.drop_index('uq_accounts_default_per_auth_config', table_name='accounts')
    op.create_index(
        'ix_unique_user_auth_config_account',
        'accounts',
        ['user_id', 'auth_config_id'],
        unique=True,
    )
    op.drop_index('ix_accounts_is_default', table_name='accounts')
    op.drop_column('accounts', 'is_default')


def data_upgrades() -> None:
    pass


def data_downgrades() -> None:
    pass
