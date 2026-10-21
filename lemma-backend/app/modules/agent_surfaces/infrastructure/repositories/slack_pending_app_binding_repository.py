"""Teammate Slack apps waiting for the account their authorisation makes."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.infrastructure.slack_manager_models import (
    SlackPendingAppBindingModel,
)

_Pending = SlackPendingAppBindingModel

#: The ``source`` of a row the reach sheet's create made.
SOURCE_WEB = "WEB"
#: The ``source`` of a row Lemma's Home tab in Slack made: its Allow is
#: finished by the account it creates, with nobody back in Lemma to do it.
SOURCE_SLACK = "SLACK"


class SlackPendingAppBindingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def for_agent(
        self, agent_id: UUID, team_id: str
    ) -> SlackPendingAppBindingModel | None:
        result = await self.session.execute(
            select(_Pending)
            .where(_Pending.agent_id == agent_id, _Pending.team_id == team_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def add(
        self,
        *,
        organization_id: UUID,
        pod_id: UUID,
        agent_id: UUID,
        auth_config_id: UUID,
        team_id: str,
        app_id: str | None,
        requested_by_user_id: UUID,
        source: str,
        slack_user_id: str | None = None,
    ) -> UUID | None:
        """The new row's id, or ``None`` when this agent already has an app
        waiting in that workspace -- the caller's app is then the duplicate."""
        result = await self.session.execute(
            insert(_Pending)
            .values(
                id=uuid4(),
                organization_id=organization_id,
                pod_id=pod_id,
                agent_id=agent_id,
                auth_config_id=auth_config_id,
                team_id=team_id,
                app_id=app_id,
                requested_by_user_id=requested_by_user_id,
                slack_user_id=slack_user_id,
                source=source,
                created_at=func.now(),
                updated_at=func.now(),
            )
            .on_conflict_do_nothing(
                constraint="uq_slack_pending_app_bindings_agent_team"
            )
            .returning(_Pending.id)
        )
        return result.scalar_one_or_none()

    async def for_auth_config(
        self, auth_config_id: UUID
    ) -> SlackPendingAppBindingModel | None:
        result = await self.session.execute(
            select(_Pending)
            .where(_Pending.auth_config_id == auth_config_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def made_before(
        self, before: datetime, *, limit: int
    ) -> list[SlackPendingAppBindingModel]:
        """Apps Lemma made that have waited since before ``before``, oldest first.
        An app the person brought themselves (no ``app_id``) is never Lemma's
        to delete."""
        result = await self.session.execute(
            select(_Pending)
            .where(_Pending.created_at < before, _Pending.app_id.is_not(None))
            .order_by(_Pending.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def delete(self, row_id: UUID) -> None:
        await self.session.execute(delete(_Pending).where(_Pending.id == row_id))
