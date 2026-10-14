"""The table of Slack app configuration tokens, one per org and workspace.

Mirrors ``migrations/versions/2026-10-14_slack_configuration_tokens_0053.py``
column for column, for the reason ``whatsapp_pool_models`` gives: a schema built
from metadata must carry the migration's guarantees, or autogenerate offers to
drop the difference.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class SlackConfigurationTokenModel(UUIDAuditBase):
    """Both tokens are ciphertext; the repository is the only thing that reads them."""

    __tablename__ = "slack_configuration_tokens"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "team_id", name="uq_slack_configuration_token_org_team"
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[str] = mapped_column(String(32), nullable=False)
    team_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    slack_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    access_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    saved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
