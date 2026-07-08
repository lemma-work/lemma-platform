"""identity email normalization and invitation reinvite uniqueness

Revision ID: 0003_identity_email_normalization
Revises: 0002_surfaces_rework
Create Date: 2026-07-08

"""

import warnings

import sqlalchemy as sa
from alembic import op

__all__ = [
    "downgrade",
    "upgrade",
    "schema_upgrades",
    "schema_downgrades",
    "data_upgrades",
    "data_downgrades",
]

# revision identifiers, used by Alembic.
revision = "0003_identity_email_normalization"
down_revision = "0002_surfaces_rework"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        with op.get_context().autocommit_block():
            data_upgrades()
            schema_upgrades()


def downgrade() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        with op.get_context().autocommit_block():
            schema_downgrades()
            data_downgrades()


def data_upgrades() -> None:
    op.execute(
        """
        WITH normalized AS (
            SELECT
                id,
                COALESCE(
                    NULLIF(
                        trim(
                            both '-' FROM regexp_replace(
                                regexp_replace(lower(trim(slug)), '[^a-z0-9]+', '-', 'g'),
                                '-{2,}',
                                '-',
                                'g'
                            )
                        ),
                        ''
                    ),
                    NULLIF(
                        trim(
                            both '-' FROM regexp_replace(
                                regexp_replace(lower(trim(name)), '[^a-z0-9]+', '-', 'g'),
                                '-{2,}',
                                '-',
                                'g'
                            )
                        ),
                        ''
                    ),
                    'org-' || replace(id::text, '-', '')
                ) AS base_slug
            FROM organizations
        ),
        ranked AS (
            SELECT
                id,
                base_slug,
                row_number() OVER (PARTITION BY base_slug ORDER BY id) AS rn
            FROM normalized
        )
        UPDATE organizations o
        SET slug = CASE
            WHEN ranked.rn = 1 THEN ranked.base_slug
            ELSE ranked.base_slug || '-' || replace(o.id::text, '-', '')
        END
        FROM ranked
        WHERE o.id = ranked.id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM users
                GROUP BY lower(email)
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Cannot normalize users.email: case-only duplicate user emails exist';
            END IF;
        END $$;
        """
    )
    op.execute("UPDATE users SET email = lower(trim(email))")
    op.execute("UPDATE organization_invitations SET email = lower(trim(email))")
    op.execute(
        """
        UPDATE organization_invitations
        SET status = 'EXPIRED', updated_at = now()
        WHERE status = 'PENDING' AND expires_at <= now()
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM organization_invitations
                WHERE status = 'PENDING'
                GROUP BY organization_id, lower(email)
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Cannot create pending invitation uniqueness: duplicate pending invitations exist for the same normalized email and organization';
            END IF;
        END $$;
        """
    )


def schema_upgrades() -> None:
    op.drop_index("ix_users_email", table_name="users")
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )

    op.drop_index("ix_org_invitation_email_org", table_name="organization_invitations")
    op.create_index(
        "uq_org_invitation_pending_email_org_lower",
        "organization_invitations",
        [sa.text("lower(email)"), "organization_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def schema_downgrades() -> None:
    op.drop_index(
        "uq_org_invitation_pending_email_org_lower",
        table_name="organization_invitations",
    )
    op.create_index(
        "ix_org_invitation_email_org",
        "organization_invitations",
        ["email", "organization_id"],
        unique=True,
    )

    op.drop_index("uq_users_email_lower", table_name="users")
    op.create_index("ix_users_email", "users", ["email"], unique=True)


def data_downgrades() -> None:
    pass
