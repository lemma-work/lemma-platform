"""surfaces rework: multi-account accounts + stable pod-unique surface name

Two changes for the agent-surfaces rework, bundled since neither has shipped
to a live DB yet:

1. accounts: drop the (user_id, auth_config_id) uniqueness so a user can
   connect several accounts to the same app (e.g. multiple Telegram bot
   tokens). Add an ``is_default`` flag (exactly one default per user/auth_config,
   enforced by a partial unique index) used when an account is resolved
   without an explicit id. Add ``display_name`` (a human-friendly label so
   several accounts of the same app can be told apart) and a partial unique
   index on ``(user_id, auth_config_id, provider_account_id)`` so the same
   provider identity can't be connected twice.

2. agent_surfaces: add ``name`` — the stable, pod-unique identifier the REST
   API now addresses surfaces by (like agent names), since a pod may have
   several surfaces of the same platform. Existing rows are backfilled to the
   lowercased platform, with a numeric suffix on any collisions. Also drops
   ``ux_agent_surfaces_pod_surface_type``, a legacy one-surface-per-platform
   uniqueness that predates the 2026-06-24 baseline squash (so it isn't
   defined in migration history) but still exists on any DB carried forward
   from before that squash — it otherwise blocks the very thing this
   migration is adding.

3. users: add a nullable ``preferences`` JSONB blob (typed ``UserPreferences``)
   holding per-user surface defaults — used to disambiguate a user reachable via
   a shared system bot/number across pods in multiple orgs.

Revision ID: 0002_surfaces_rework
Revises: 0001_baseline
Create Date: 2026-07-01

"""

import warnings

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

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
    # A human-friendly label so a user can tell several accounts of the same app
    # apart (e.g. "@lemmabot", "rahul@gmail.com", "+1 555…"); derived at connect.
    op.add_column(
        'accounts',
        sa.Column('display_name', sa.String(length=255), nullable=True),
    )
    # One account per provider identity per (user, auth_config): reject connecting
    # the same underlying account twice. Partial (provider_account_id NOT NULL) so
    # accounts whose identity couldn't be derived don't collide on NULL.
    op.create_index(
        'uq_accounts_provider_identity',
        'accounts',
        ['user_id', 'auth_config_id', 'provider_account_id'],
        unique=True,
        postgresql_where=sa.text('provider_account_id IS NOT NULL'),
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
    # Superseded by uq_agent_surface_pod_name above. It's a unique INDEX, not a
    # table constraint (created via CREATE UNIQUE INDEX pre-squash), so this
    # must be DROP INDEX, not DROP CONSTRAINT. Not part of any migration here
    # (pre-dates the baseline squash), so it's only present on DBs carried
    # forward from before 2026-06-24 — IF EXISTS covers fresh DBs too.
    op.execute(
        "DROP INDEX IF EXISTS ux_agent_surfaces_pod_surface_type"
    )

    # --- users: typed JSON preferences blob (e.g. per-platform default surface
    # used to disambiguate a user reachable via a shared system bot across pods
    # in multiple orgs). Nullable; absent means "no preferences set". ---
    op.add_column(
        'users',
        sa.Column(
            'preferences',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def schema_downgrades() -> None:
    op.drop_column('users', 'preferences')

    # Restores the legacy unique index dropped in schema_upgrades(). Fails if
    # any pod now has two surfaces of the same platform — correct: that data
    # can't be represented under the old one-per-platform rule being restored.
    op.create_index(
        'ux_agent_surfaces_pod_surface_type', 'agent_surfaces', ['pod_id', 'surface_type'],
        unique=True,
    )
    op.drop_constraint('uq_agent_surface_pod_name', 'agent_surfaces', type_='unique')
    op.drop_index('ix_agent_surfaces_name', table_name='agent_surfaces')
    op.drop_column('agent_surfaces', 'name')

    op.drop_index('uq_accounts_provider_identity', table_name='accounts')
    op.drop_column('accounts', 'display_name')
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
