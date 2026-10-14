"""Slack configuration tokens, encrypted at rest; the only reader of the columns."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_secret_cipher
from app.modules.agent_surfaces.infrastructure.slack_configuration_models import (
    SlackConfigurationTokenModel,
)
from app.modules.agent_surfaces.platforms.slack.app_configuration import (
    RotatedConfigurationToken,
)


#: An organization keeps a token per Slack workspace it makes apps in: a
#: handful, so this is a ceiling on the read rather than a page size.
MAX_WORKSPACES_PER_ORGANIZATION = 50


class SlackConfigurationTokenRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_organization(
        self, organization_id: UUID
    ) -> list[SlackConfigurationTokenModel]:
        result = await self.session.execute(
            select(SlackConfigurationTokenModel)
            .where(SlackConfigurationTokenModel.organization_id == organization_id)
            .order_by(SlackConfigurationTokenModel.created_at)
            .limit(MAX_WORKSPACES_PER_ORGANIZATION)
        )
        return list(result.scalars().all())

    async def get(
        self, organization_id: UUID, team_id: str
    ) -> SlackConfigurationTokenModel | None:
        result = await self.session.execute(
            select(SlackConfigurationTokenModel).where(
                SlackConfigurationTokenModel.organization_id == organization_id,
                SlackConfigurationTokenModel.team_id == team_id,
            )
        )
        return result.scalar_one_or_none()

    async def save(
        self,
        *,
        organization_id: UUID,
        token: RotatedConfigurationToken,
        team_name: str | None = None,
        saved_by_user_id: UUID | None = None,
    ) -> SlackConfigurationTokenModel:
        """Keep the newest pair for this workspace, replacing any older one."""
        cipher = get_secret_cipher()
        row = await self.get(organization_id, token.team_id)
        if row is None:
            row = SlackConfigurationTokenModel(
                organization_id=organization_id, team_id=token.team_id
            )
            self.session.add(row)
        row.access_token = cipher.encrypt_str(token.access_token)
        row.refresh_token = cipher.encrypt_str(token.refresh_token)
        row.access_expires_at = token.expires_at
        row.slack_user_id = token.slack_user_id or row.slack_user_id
        row.team_name = team_name or row.team_name
        if saved_by_user_id is not None:
            row.saved_by_user_id = saved_by_user_id
        row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return row

    async def delete(self, organization_id: UUID, team_id: str) -> bool:
        result = await self.session.execute(
            delete(SlackConfigurationTokenModel).where(
                SlackConfigurationTokenModel.organization_id == organization_id,
                SlackConfigurationTokenModel.team_id == team_id,
            )
        )
        return bool(result.rowcount)


def reveal_tokens(row: SlackConfigurationTokenModel) -> tuple[str, str]:
    """``(access_token, refresh_token)``, decrypted."""
    cipher = get_secret_cipher()
    return (
        cipher.decrypt_str(row.access_token) or "",
        cipher.decrypt_str(row.refresh_token) or "",
    )
