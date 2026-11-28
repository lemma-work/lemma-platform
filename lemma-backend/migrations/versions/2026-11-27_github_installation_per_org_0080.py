"""A GitHub App installation may be bound to several organizations, one row each.

GitHub allows one installation of an App per GitHub account. A GitHub
organization worked on from two Lemma organizations has one installation, and
``UNIQUE (connector_id, external_id)`` let only one of them hold it. The rule
becomes one row per (installation, organization): each row is still earned on
its own claim, with GitHub's proof that the claimant controls the installation
(``services/auth/github_bindings.py``).

Only the constraint changes. Every existing row is valid under the new one, and
its leading columns still serve the lookups by installation that the old one
did. The downgrade first deletes the rows an organization let go of
(``metadata ? 'released_at'``): the old code has no notion of letting go and
would read one as a binding. Then it keeps the oldest remaining binding of each
installation and deletes the others; the accounts on those keep naming it and
mint nothing for it, as any account on an installation its organization does
not hold.

Revision ID: 0080_github_installation_per_org
Revises: 0079_datastore_hooks
Create Date: 2026-11-27
"""

from __future__ import annotations

from alembic import op

revision = "0080_github_installation_per_org"
down_revision = "0079_datastore_hooks"
branch_labels = None
depends_on = None

_TABLE = "connector_installations"
_OLD = "uq_connector_installations_connector_external"
_NEW = "uq_connector_installations_connector_external_org"


def upgrade() -> None:
    op.drop_constraint(_OLD, _TABLE, type_="unique")
    op.create_unique_constraint(
        _NEW, _TABLE, ["connector_id", "external_id", "organization_id"]
    )


def downgrade() -> None:
    # A released row is kept only so the next read cannot take the
    # installation back. Before the oldest row is chosen, so a let-go row
    # never survives in place of a live one.
    op.execute(f"DELETE FROM {_TABLE} WHERE metadata ? 'released_at'")
    op.execute(
        f"""
        DELETE FROM {_TABLE} later
        USING {_TABLE} earlier
        WHERE later.connector_id = earlier.connector_id
          AND later.external_id = earlier.external_id
          AND (later.created_at, later.id) > (earlier.created_at, earlier.id)
        """
    )
    op.drop_constraint(_NEW, _TABLE, type_="unique")
    op.create_unique_constraint(_OLD, _TABLE, ["connector_id", "external_id"])
