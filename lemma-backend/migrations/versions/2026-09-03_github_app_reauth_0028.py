"""Every native GitHub account has to be reconnected against the GitHub App.

The connector used to authorize through a GitHub OAuth App: one client id, one
user token, no installation. It now authorizes through a GitHub *App*, which is
a different client id issuing tokens against a different consent, and the
connect URL is the App's installation page rather than
``github.com/login/oauth/authorize``. Tokens minted under the old client are not
merely stale -- they belong to an application the deployment no longer holds the
secret for, so nothing can refresh them and nothing can revoke them from here.

They are also missing the piece that makes the new model work: an account bound
to an installation, in ``external_ref``. Without it a GitHub App cannot mint an
installation token, so an agent's operations would silently keep running as the
old user token until it expired.

**Marked, not deleted.** The status is what forces the reconnect; deleting the
rows would achieve the same thing and take four un-foreign-keyed references with
it -- tool grants, a conversation's ``metadata.repo.account_id``, pod bundle
bindings, and publish's required ``account_id`` -- silently disconnecting
sandboxes and pod publishing that work today. The catalog importer refuses to
delete accounts for exactly this reason, and this follows it.

Composio-brokered GitHub accounts are untouched: Composio holds that connection
and the App switch says nothing about it.

Reversing this cannot restore CONNECTED, because by then it would be a lie
about a credential that no longer works. The downgrade is deliberately a no-op.

Revision ID: 0028_github_app_reauth
Revises: 0027_schedule_account_set_null
"""

from alembic import op

revision = "0028_github_app_reauth"
down_revision = "0027_schedule_account_set_null"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE accounts
        SET status = 'REAUTH_REQUIRED',
            external_ref = NULL
        FROM auth_configs
        WHERE accounts.auth_config_id = auth_configs.id
          AND auth_configs.connector_id = 'github'
          AND auth_configs.kind <> 'composio'
          AND accounts.status <> 'REAUTH_REQUIRED'
        """
    )


def downgrade() -> None:
    """Deliberately empty -- see the module docstring."""
