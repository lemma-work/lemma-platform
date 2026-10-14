"""The manager's tables. Mirrors ``2026-10-14_slack_manager_0054.py`` column for
column, for the reason ``whatsapp_pool_models`` gives."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class SlackManagerInstallModel(UUIDAuditBase):
    """Lemma's own app in one workspace, and the organization it belongs to."""

    __tablename__ = "slack_manager_installs"

    team_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    team_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    bot_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    installed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class SlackWorkspacePersonModel(UUIDAuditBase):
    """A Slack user proven to be this Lemma person."""

    __tablename__ = "slack_workspace_people"
    __table_args__ = (
        UniqueConstraint("team_id", "slack_user_id", name="uq_slack_workspace_person"),
    )

    team_id: Mapped[str] = mapped_column(String(32), nullable=False)
    slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )


class SlackPendingAppBindingModel(UUIDAuditBase):
    """A teammate's app made from Slack, waiting for the account its Allow makes."""

    __tablename__ = "slack_pending_app_bindings"

    auth_config_id: Mapped[UUID] = mapped_column(nullable=False, unique=True)
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[str] = mapped_column(String(32), nullable=False)
    app_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    slack_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
