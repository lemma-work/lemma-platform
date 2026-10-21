"""The table of Slack app configuration tokens, one per org and workspace.

Created by ``migrations/versions/2026-10-20_slack_apps_0060.py``, whose DDL is
generated from this model and pinned to it by
``tests/unit/test_slack_apps_ddl_matches_models.py``: the e2e schema is built
from the models and a deploy runs the migration, so the two must not differ.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class SlackConfigurationTokenModel(UUIDAuditBase):
    """Both tokens are ciphertext; the repository is the only thing that reads them.

    Slack refresh tokens are treated as single-use, so the row is also the
    rotation's coordination point: ``generation`` moves on every pair stored,
    ``rotation_claimed_until`` is the one rotator's lease on the database
    clock, and ``refused_at`` records that Slack stopped accepting the stored
    refresh token, until somebody pastes a new one.
    """

    __tablename__ = "slack_configuration_tokens"
    __table_args__ = (
        # Per (organization, workspace): several organizations may each make
        # their own teammates' apps in one Slack workspace. Only Lemma's own
        # manager app binds a workspace to a single organization.
        UniqueConstraint(
            "organization_id", "team_id", name="uq_slack_configuration_token_org_team"
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[str] = mapped_column(String(32), nullable=False)
    team_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Whose token it is: Slack creates every app under the person who
    #: generated it.
    slack_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    access_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    generation: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    rotation_claimed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When a person last pasted it. Rotation moves ``updated_at``, not this.
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    saved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
