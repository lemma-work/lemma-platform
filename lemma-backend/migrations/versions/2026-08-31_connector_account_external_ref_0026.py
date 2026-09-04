"""Give a connected account somewhere to say which upstream tenant it speaks for.

Jira resolves a ``cloud_id``, Teams decodes a ``tid``, Slack's token response
carries ``team.id``, Composio hands back a ``connection_id``, and a GitHub App
will carry an ``installation_id``. All of them answer the same question -- which
upstream thing does this authorization speak for -- and every one currently
lives inside ``accounts.credentials``, which is encrypted JSONB. Encrypted JSONB
cannot be indexed and cannot be matched against an inbound webhook payload,
which is the stated reason the webhook route rejects every source it cannot
attribute to a tenant.

On the account rather than the organization's install, deliberately: one Slack
app can be installed in many workspaces and one GitHub App in many
organizations, all under a single auth config, so the tenant is whatever the
individual authorization was for. An install-level column would have been
correct only until the second workspace connected.

Not unique, for the same reason it is not on the install: every member of an
organization authorizing the same GitHub App shares one installation id. It is a
routing key, not an identity -- the identity is ``provider_account_id``, which
keeps its own uniqueness index.

Backfilled from existing credentials by ``scripts/backfill_account_external_ref.py``,
not here: the values sit inside an encrypted column, so reading them needs the
application's cipher rather than SQL.
"""

import sqlalchemy as sa
from alembic import op


# `alembic_version.version_num` is varchar(32); a longer id fails the
# version bump at the very end of the upgrade and rolls the whole thing back.
revision = "0026_account_external_ref"
down_revision = "0025_pod_default_agent_row"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts", sa.Column("external_ref", sa.String(length=255), nullable=True)
    )
    op.create_index(
        "ix_accounts_external_ref", "accounts", ["external_ref"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_accounts_external_ref", table_name="accounts")
    op.drop_column("accounts", "external_ref")
