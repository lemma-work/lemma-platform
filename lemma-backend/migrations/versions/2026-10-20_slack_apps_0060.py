"""Slack apps Lemma makes, and Lemma's own app in a workspace.

Every table the Slack setup stack needs, in one revision:

* **`slack_configuration_tokens`** -- an organization's Slack app
  configuration token per workspace, both halves encrypted with the secret
  cipher and listed in ``app.core.crypto.rotation.REGISTRY``. It is what lets
  Lemma create a teammate's own app (``apps.manifest.create``). Refresh tokens
  are treated as single-use: ``generation`` is compared and bumped on every
  stored pair, ``rotation_claimed_until`` is one rotator's lease on the
  database clock, ``refused_at`` marks a stored token Slack no longer accepts,
  and ``saved_at`` is when a person last pasted one.
* **`slack_pending_app_bindings`** -- a teammate's app waiting for the account
  its authorisation makes, one per agent per workspace. Creating an app writes
  one, a retried create answers from it, and a periodic sweep deletes the apps
  nobody authorised.
* **`slack_manager_installs`** -- Lemma's own app in a workspace, and the one
  organization it belongs to.
* **`slack_workspace_people`** -- Slack users proven to be Lemma people across
  a workspace, by the install itself or by Sign in with Slack.

Organizations per workspace: configuration tokens are per (organization,
workspace), so several organizations may each make their own teammates' apps
in one Slack workspace. Only the manager binding is one organization per
workspace. The two rules answer different questions and do not conflict.

None of these tables has shipped, so nothing is carried over. The downgrade
drops them in reverse dependency order.

Revision ID: 0060_slack_apps
Revises: 0059_drop_old_transport
"""

from alembic import op

revision = "0060_slack_apps"
down_revision = "0059_drop_old_transport"
branch_labels = None
depends_on = None

#: The tables this creates, as ``agent_surfaces.infrastructure``'s
#: ``slack_configuration_models`` and ``slack_manager_models`` declare them --
#: generated from the models, and pinned to them by a unit test.
_NEW_TABLES = """
CREATE TABLE slack_configuration_tokens (
	organization_id UUID NOT NULL,
	team_id VARCHAR(32) NOT NULL,
	team_name VARCHAR(255),
	slack_user_id VARCHAR(32),
	access_token TEXT NOT NULL,
	refresh_token TEXT NOT NULL,
	access_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	generation INTEGER DEFAULT 0 NOT NULL,
	rotation_claimed_until TIMESTAMP WITH TIME ZONE,
	refused_at TIMESTAMP WITH TIME ZONE,
	saved_at TIMESTAMP WITH TIME ZONE NOT NULL,
	saved_by_user_id UUID,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_slack_configuration_token_org_team UNIQUE (organization_id, team_id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
	FOREIGN KEY(saved_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);
CREATE TABLE slack_manager_installs (
	team_id VARCHAR(32) NOT NULL,
	team_name VARCHAR(255),
	organization_id UUID NOT NULL,
	account_id UUID NOT NULL,
	bot_user_id VARCHAR(32) NOT NULL,
	installed_by_user_id UUID,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_slack_manager_installs_team UNIQUE (team_id),
	CONSTRAINT uq_slack_manager_installs_account UNIQUE (account_id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
	FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE,
	FOREIGN KEY(installed_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX ix_slack_manager_installs_org ON slack_manager_installs (organization_id, created_at);
CREATE TABLE slack_workspace_people (
	team_id VARCHAR(32) NOT NULL,
	slack_user_id VARCHAR(32) NOT NULL,
	user_id UUID NOT NULL,
	proved_by VARCHAR(10) NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_slack_workspace_person UNIQUE (team_id, slack_user_id),
	CONSTRAINT uq_slack_workspace_person_user UNIQUE (team_id, user_id),
	CONSTRAINT ck_slack_workspace_person_proved_by CHECK (proved_by IN ('INSTALL', 'OIDC')),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_slack_workspace_people_user ON slack_workspace_people (user_id);
CREATE TABLE slack_pending_app_bindings (
	organization_id UUID NOT NULL,
	pod_id UUID NOT NULL,
	agent_id UUID NOT NULL,
	auth_config_id UUID NOT NULL,
	team_id VARCHAR(32) NOT NULL,
	app_id VARCHAR(32),
	requested_by_user_id UUID NOT NULL,
	slack_user_id VARCHAR(32),
	source VARCHAR(10) NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_slack_pending_app_bindings_agent_team UNIQUE (agent_id, team_id),
	CONSTRAINT uq_slack_pending_app_bindings_auth_config UNIQUE (auth_config_id),
	CONSTRAINT fk_slack_pending_app_binding_pod_org FOREIGN KEY(pod_id, organization_id) REFERENCES pods (id, organization_id) ON DELETE CASCADE ON UPDATE CASCADE,
	CONSTRAINT ck_slack_pending_app_bindings_source CHECK (source IN ('WEB', 'SLACK')),
	FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE,
	FOREIGN KEY(auth_config_id) REFERENCES auth_configs (id) ON DELETE CASCADE,
	FOREIGN KEY(requested_by_user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_slack_pending_app_bindings_pod ON slack_pending_app_bindings (pod_id);
"""

#: In creation order; the downgrade drops them in reverse.
_NEW_TABLE_NAMES = (
    "slack_configuration_tokens",
    "slack_manager_installs",
    "slack_workspace_people",
    "slack_pending_app_bindings",
)


def upgrade() -> None:
    for statement in _NEW_TABLES.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_NEW_TABLE_NAMES):
        op.drop_table(table)
