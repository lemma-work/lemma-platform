"""Lemma's own Slack app as the manager: its install, its people, its handoffs.

One Slack app is one bot user, so each teammate answers in Slack through an app
of its own. Lemma's own app stops answering and starts managing: it lists a
workspace's teammates, makes their apps, and hands a channel to the teammate
someone invited it for. Three tables let it:

* ``slack_manager_installs`` -- Lemma's app installed in a workspace, and the
  organization that workspace belongs to. One organization per workspace,
  which is what lets everyone in the workspace be known to that organization.
  The bot token itself stays on the connector account it was authorised as.
* ``slack_workspace_people`` -- a Slack user proven to be a Lemma person: they
  opened a link only their own App Home shows, and signed in. Managing anything
  from Slack needs this; a profile email anyone with admin rights can edit does
  not count.
* ``slack_pending_app_bindings`` -- a teammate's app Lemma created from Slack,
  waiting for its Allow. When the account authorised against that install
  arrives, the pod's surface is made from it, so nobody has to come back to
  Lemma to finish.

Revision ID: 0054_slack_manager
Revises: 0053_slack_configuration_tokens
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0054_slack_manager"
down_revision = "0053_slack_configuration_tokens"
branch_labels = None
depends_on = None


def _audit() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "slack_manager_installs",
        *_audit(),
        sa.Column("team_id", sa.String(32), nullable=False, unique=True),
        sa.Column("team_name", sa.String(255), nullable=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bot_user_id", sa.String(32), nullable=False),
        sa.Column(
            "installed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_table(
        "slack_workspace_people",
        *_audit(),
        sa.Column("team_id", sa.String(32), nullable=False),
        sa.Column("slack_user_id", sa.String(32), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.UniqueConstraint(
            "team_id", "slack_user_id", name="uq_slack_workspace_person"
        ),
    )
    op.create_table(
        "slack_pending_app_bindings",
        *_audit(),
        sa.Column(
            "auth_config_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "pod_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("team_id", sa.String(32), nullable=False),
        sa.Column("app_id", sa.String(32), nullable=True),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slack_user_id", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("slack_pending_app_bindings")
    op.drop_table("slack_workspace_people")
    op.drop_table("slack_manager_installs")
