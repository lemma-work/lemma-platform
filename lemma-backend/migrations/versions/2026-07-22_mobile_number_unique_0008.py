"""Make normalized profile mobile numbers globally unique.

Revision ID: 0008_mobile_number_unique
Revises: 0007_auth_hardening
"""

import sqlalchemy as sa
from alembic import op


revision = "0008_mobile_number_unique"
down_revision = "0007_auth_hardening"

_DIGITS_SQL = "regexp_replace(mobile_number, '\\D', '', 'g')"
_NONEMPTY_MOBILE_SQL = f"mobile_number IS NOT NULL AND {_DIGITS_SQL} <> ''"


def upgrade() -> None:
    # Preserve one deterministic owner for legacy duplicates. A verified owner
    # wins; otherwise the oldest profile wins. Clear stale surface cache rows
    # before clearing the losing profile values.
    op.execute(
        sa.text(
            f"""
            WITH ranked_mobile_owners AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY {_DIGITS_SQL}
                        ORDER BY
                            (mobile_verified_at IS NOT NULL) DESC,
                            created_at ASC,
                            id ASC
                    ) AS owner_rank
                FROM users
                WHERE {_NONEMPTY_MOBILE_SQL}
            )
            UPDATE agent_surface_external_users
            SET resolved_user_id = NULL
            WHERE resolved_user_id IN (
                SELECT id
                FROM ranked_mobile_owners
                WHERE owner_rank > 1
            )
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            WITH ranked_mobile_owners AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY {_DIGITS_SQL}
                        ORDER BY
                            (mobile_verified_at IS NOT NULL) DESC,
                            created_at ASC,
                            id ASC
                    ) AS owner_rank
                FROM users
                WHERE {_NONEMPTY_MOBILE_SQL}
            )
            UPDATE users
            SET mobile_number = NULL, mobile_verified_at = NULL
            WHERE id IN (
                SELECT id
                FROM ranked_mobile_owners
                WHERE owner_rank > 1
            )
            """
        )
    )
    op.drop_index("uq_users_verified_mobile_e164", table_name="users")
    op.create_index(
        "uq_users_mobile_number_digits",
        "users",
        [sa.text(_DIGITS_SQL)],
        unique=True,
        postgresql_where=sa.text(_NONEMPTY_MOBILE_SQL),
    )


def downgrade() -> None:
    op.drop_index("uq_users_mobile_number_digits", table_name="users")
    op.create_index(
        "uq_users_verified_mobile_e164",
        "users",
        ["mobile_number"],
        unique=True,
        postgresql_where=sa.text("mobile_verified_at IS NOT NULL"),
    )
