"""Slack configuration tokens: what lets Lemma make a pod its own Slack app.

One Slack app is one bot user, so every teammate on Slack needs an app of its
own. Slack's `apps.manifest.create` makes one from a manifest, but only with an
app configuration token -- which no OAuth flow issues. An admin generates it
once per workspace on api.slack.com and pastes it into Lemma; from then on
Lemma creates each pod's app and the person only clicks Allow.

One row per (organization, Slack workspace). Both tokens are encrypted at rest
with the secret cipher, as connector credentials are; they move to the secrets
vault with everything else when it lands. The access token lasts 12 hours and
is rotated from the refresh token before use, so `access_expires_at` is what
says whether it is still good.

Revision ID: 0053_slack_configuration_tokens
Revises: 0052_workflow_run_times_utc
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0053_slack_configuration_tokens"
down_revision = "0052_workflow_run_times_utc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "slack_configuration_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("team_id", sa.String(32), nullable=False),
        sa.Column("team_name", sa.String(255), nullable=True),
        sa.Column("slack_user_id", sa.String(32), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "saved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "organization_id", "team_id", name="uq_slack_configuration_token_org_team"
        ),
    )


def downgrade() -> None:
    op.drop_table("slack_configuration_tokens")
