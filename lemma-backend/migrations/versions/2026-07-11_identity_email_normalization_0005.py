"""Normalize identity emails and allow reinviting historical invitees.

Revision ID: 0005_identity_normalization
Revises: 0004_datastore_file_integrity
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision = "0005_identity_normalization"
down_revision = "0004_datastore_file_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _assert_normalized_user_emails_are_unique() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM users
                GROUP BY lower(btrim(email))
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Cannot normalize users.email: duplicate normalized user emails exist';
            END IF;
        END $$;
        """
    )


def _expire_stale_invitations() -> None:
    op.execute(
        """
        UPDATE organization_invitations
        SET status = 'EXPIRED', updated_at = now()
        WHERE status = 'PENDING' AND expires_at <= now()
        """
    )


def _assert_pending_invitation_emails_are_unique() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM organization_invitations
                WHERE status = 'PENDING'
                GROUP BY organization_id, lower(btrim(email))
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Cannot normalize pending invitations: duplicate normalized emails exist in an organization';
            END IF;
        END $$;
        """
    )


def _normalize_organization_slugs() -> None:
    # The old unique index is removed before this runs. That avoids transient
    # conflicts when one normalized value is another row's current slug.
    op.execute(
        """
        CREATE TEMPORARY TABLE organization_slug_backfill (
            id uuid PRIMARY KEY,
            base_slug varchar(255) NOT NULL,
            final_slug varchar(255) UNIQUE
        ) ON COMMIT DROP
        """
    )
    op.execute(
        """
        INSERT INTO organization_slug_backfill (id, base_slug)
        SELECT
            id,
            left(
                COALESCE(
                    NULLIF(
                        trim(
                            both '-' FROM regexp_replace(
                                lower(btrim(slug)),
                                '[^a-z0-9]+',
                                '-',
                                'g'
                            )
                        ),
                        ''
                    ),
                    NULLIF(
                        trim(
                            both '-' FROM regexp_replace(
                                lower(btrim(name)),
                                '[^a-z0-9]+',
                                '-',
                                'g'
                            )
                        ),
                        ''
                    ),
                    'org-' || replace(id::text, '-', '')
                ),
                255
            )
        FROM organizations
        """
    )
    op.execute(
        """
        DO $$
        DECLARE
            current_row record;
            candidate varchar(255);
            suffix text;
            attempt integer;
        BEGIN
            FOR current_row IN
                SELECT id, base_slug
                FROM organization_slug_backfill
                ORDER BY id
            LOOP
                candidate := current_row.base_slug;
                suffix := '-' || replace(current_row.id::text, '-', '');
                attempt := 0;

                WHILE EXISTS (
                    SELECT 1
                    FROM organization_slug_backfill
                    WHERE final_slug = candidate
                ) LOOP
                    attempt := attempt + 1;
                    candidate :=
                        left(
                            current_row.base_slug,
                            255 - length(suffix) - length(attempt::text) - 1
                        )
                        || suffix || '-' || attempt::text;
                END LOOP;

                UPDATE organization_slug_backfill
                SET final_slug = candidate
                WHERE id = current_row.id;
            END LOOP;
        END $$;
        """
    )
    op.execute(
        """
        UPDATE organizations AS organization
        SET slug = backfill.final_slug
        FROM organization_slug_backfill AS backfill
        WHERE organization.id = backfill.id
        """
    )


def upgrade() -> None:
    _assert_normalized_user_emails_are_unique()
    _expire_stale_invitations()
    _assert_pending_invitation_emails_are_unique()

    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index(
        "ix_org_invitation_email_org",
        table_name="organization_invitations",
    )

    _normalize_organization_slugs()
    op.execute("UPDATE users SET email = lower(btrim(email))")
    op.execute("UPDATE organization_invitations SET email = lower(btrim(email))")

    op.create_index(
        "ix_organizations_slug",
        "organizations",
        ["slug"],
        unique=True,
    )
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )
    op.create_index(
        "uq_org_invitation_pending_email_org_lower",
        "organization_invitations",
        [sa.text("lower(email)"), "organization_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM organization_invitations
                GROUP BY email, organization_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade invitation uniqueness while historical duplicate invitations exist';
            END IF;
        END $$;
        """
    )
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
