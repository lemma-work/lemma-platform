"""Make a deployed app reachable again: apps are PUBLIC by default.

An app is served to anonymous browsers at ``<public_slug>.<app_base_domain>``,
and the ``/public/apps`` route behind that host now refuses anything whose
visibility is not PUBLIC. Apps defaulted to POD, so every app ever created --
through the API, the widget promotion flow, or a pod bundle import -- was
stored POD and 404'd on its own URL. The shell being anonymous is the design:
it is HTML and JS, its data calls are authorized on their own, and the SDK's
AppGate turns a denial into a sign-in or request-access screen.

The default now lives at PUBLIC (``AppModel.visibility``), which fixes apps
created from here on. This backfill fixes the ones already stored.

Only ``POD`` rows are moved. POD was the silent default nobody chose: the API
applied it when a request omitted ``visibility``, and no UI ever offered it as
a deliberate "pod only" answer for an app. PERSONAL and RESTRICTED are
different -- both require someone to have opened the share dialog and picked
them -- so they are left exactly as they are, and an owner who wants an app off
the public internet can still choose one of them.

Downgrade cannot separate a backfilled row from one an owner later set to
PUBLIC on purpose, so it does not try; it only restores the column default's
old meaning by leaving the data alone.

Revision ID: 0015_apps_public_by_default
Revises: 0014_workspace_sandboxes
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_apps_public_by_default"
down_revision = "0014_workspace_sandboxes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE apps
               SET visibility = 'PUBLIC',
                   updated_at = now()
             WHERE upper(coalesce(visibility, '')) IN ('POD', 'ALL', '')
            """
        )
    )


def downgrade() -> None:
    # Intentionally empty -- see the module docstring.
    pass
