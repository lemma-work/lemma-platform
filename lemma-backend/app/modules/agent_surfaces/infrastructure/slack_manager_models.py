"""Lemma's own Slack app in a workspace, the people it proved, and teammate
apps waiting to be authorised.

Created by ``migrations/versions/2026-10-20_slack_apps_0060.py`` with
``slack_configuration_tokens``; the DDL there is generated from these models
and pinned to them by ``tests/unit/test_slack_apps_ddl_matches_models.py``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class SlackManagerInstallModel(UUIDAuditBase):
    """Lemma's own app in one workspace, and the one organization it belongs to.

    One organization per workspace, unlike configuration tokens: the manager
    is where a workspace's people meet the organization, and two of them in
    one workspace could not both answer its Home tab. ``account_id`` is the
    connector account the install made -- an organization's, not a pod's, so
    not an ``external_accounts`` row.
    """

    __tablename__ = "slack_manager_installs"
    __table_args__ = (
        UniqueConstraint("team_id", name="uq_slack_manager_installs_team"),
        # One account binds one workspace; also the index the account's
        # cascade walks.
        UniqueConstraint("account_id", name="uq_slack_manager_installs_account"),
        Index("ix_slack_manager_installs_org", "organization_id", "created_at"),
    )

    team_id: Mapped[str] = mapped_column(String(32), nullable=False)
    team_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    bot_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    installed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class SlackWorkspacePersonModel(UUIDAuditBase):
    """A Slack user proven to be this Lemma person, across the whole workspace.

    Not ``surface_verified_identities``: that table is scoped to one
    installation and written after an emailed code. This is proof by Slack
    itself -- the installer's own OAuth grant (``INSTALL``) or Sign in with
    Slack (``OIDC``) -- and outlives a reinstall, so it has no foreign key to
    ``slack_manager_installs``.
    """

    __tablename__ = "slack_workspace_people"
    __table_args__ = (
        UniqueConstraint("team_id", "slack_user_id", name="uq_slack_workspace_person"),
        UniqueConstraint("team_id", "user_id", name="uq_slack_workspace_person_user"),
        CheckConstraint(
            "proved_by IN ('INSTALL', 'OIDC')",
            name="ck_slack_workspace_person_proved_by",
        ),
        Index("ix_slack_workspace_people_user", "user_id"),
    )

    team_id: Mapped[str] = mapped_column(String(32), nullable=False)
    slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    proved_by: Mapped[str] = mapped_column(String(10), nullable=False)


class SlackPendingAppBindingModel(UUIDAuditBase):
    """A teammate's Slack app waiting for the account its Allow makes.

    One per agent per workspace, which is what makes creating the app
    idempotent: a retried create finds this row and answers with the app it
    names. ``source`` says which flow made it -- ``WEB``, Lemma creating the
    app from the reach sheet, or ``SLACK``, from Lemma's Home tab. ``app_id`` is
    null when the person brought an app of their own, which Lemma never
    deletes. The unauthorised-app sweep reads its age from ``created_at``.
    """

    __tablename__ = "slack_pending_app_bindings"
    __table_args__ = (
        UniqueConstraint(
            "agent_id", "team_id", name="uq_slack_pending_app_bindings_agent_team"
        ),
        UniqueConstraint(
            "auth_config_id", name="uq_slack_pending_app_bindings_auth_config"
        ),
        ForeignKeyConstraint(
            ["pod_id", "organization_id"],
            ["pods.id", "pods.organization_id"],
            name="fk_slack_pending_app_binding_pod_org",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        CheckConstraint(
            "source IN ('WEB', 'SLACK')", name="ck_slack_pending_app_bindings_source"
        ),
        Index("ix_slack_pending_app_bindings_pod", "pod_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(nullable=False)
    pod_id: Mapped[UUID] = mapped_column(nullable=False)
    #: The agent the app answers as; the pod's own agent's id is the pod's.
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    auth_config_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_configs.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[str] = mapped_column(String(32), nullable=False)
    app_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: Who asked, when the request came from Slack.
    slack_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
