"""The manager's install, the Slack people it knows, and its pending handoffs."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.infrastructure.repositories.slack_configuration_token_repository import (
    MAX_WORKSPACES_PER_ORGANIZATION,
)
from app.modules.agent_surfaces.infrastructure.slack_manager_models import (
    SlackManagerInstallModel,
    SlackPendingAppBindingModel,
    SlackWorkspacePersonModel,
)


class SlackManagerRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # -- the install ---------------------------------------------------------

    async def install_for_team(self, team_id: str) -> SlackManagerInstallModel | None:
        result = await self.session.execute(
            select(SlackManagerInstallModel).where(
                SlackManagerInstallModel.team_id == team_id
            )
        )
        return result.scalar_one_or_none()

    async def installs_for_organization(
        self, organization_id: UUID
    ) -> list[SlackManagerInstallModel]:
        result = await self.session.execute(
            select(SlackManagerInstallModel)
            .where(SlackManagerInstallModel.organization_id == organization_id)
            .order_by(SlackManagerInstallModel.created_at)
            .limit(MAX_WORKSPACES_PER_ORGANIZATION)
        )
        return list(result.scalars().all())

    async def save_install(
        self,
        *,
        team_id: str,
        team_name: str | None,
        organization_id: UUID,
        account_id: UUID,
        bot_user_id: str,
        installed_by_user_id: UUID | None,
    ) -> SlackManagerInstallModel:
        """Bind the workspace to the organization, or refresh the binding.

        One organization per workspace: a second organization installing Lemma
        into a workspace already bound is refused, not quietly moved, because
        everyone in that workspace is known to the first.
        """
        row = await self.install_for_team(team_id)
        if row is not None and row.organization_id != organization_id:
            raise AgentSurfaceValidationError(
                "This Slack workspace already belongs to another Lemma "
                "organization. Remove Lemma from it there first."
            )
        if row is None:
            row = SlackManagerInstallModel(team_id=team_id)
            self.session.add(row)
        row.team_name = team_name or row.team_name
        row.organization_id = organization_id
        row.account_id = account_id
        row.bot_user_id = bot_user_id
        if installed_by_user_id is not None:
            row.installed_by_user_id = installed_by_user_id
        row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return row

    async def delete_install(self, team_id: str) -> bool:
        result = await self.session.execute(
            delete(SlackManagerInstallModel).where(
                SlackManagerInstallModel.team_id == team_id
            )
        )
        return bool(result.rowcount)

    # -- people --------------------------------------------------------------

    async def person(self, team_id: str, slack_user_id: str) -> UUID | None:
        result = await self.session.execute(
            select(SlackWorkspacePersonModel.user_id).where(
                SlackWorkspacePersonModel.team_id == team_id,
                SlackWorkspacePersonModel.slack_user_id == slack_user_id,
            )
        )
        return result.scalar_one_or_none()

    async def link_person(
        self, *, team_id: str, slack_user_id: str, user_id: UUID
    ) -> None:
        """Record that this Slack user is this person. A newer proof replaces
        an older one, as signing in as someone else on a shared machine would."""
        result = await self.session.execute(
            select(SlackWorkspacePersonModel).where(
                SlackWorkspacePersonModel.team_id == team_id,
                SlackWorkspacePersonModel.slack_user_id == slack_user_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = SlackWorkspacePersonModel(
                team_id=team_id, slack_user_id=slack_user_id
            )
            self.session.add(row)
        row.user_id = user_id
        row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()

    # -- pending app bindings -----------------------------------------------

    async def add_pending_binding(
        self,
        *,
        auth_config_id: UUID,
        pod_id: UUID,
        team_id: str,
        app_id: str | None,
        requested_by_user_id: UUID,
        slack_user_id: str | None,
    ) -> None:
        self.session.add(
            SlackPendingAppBindingModel(
                auth_config_id=auth_config_id,
                pod_id=pod_id,
                team_id=team_id,
                app_id=app_id,
                requested_by_user_id=requested_by_user_id,
                slack_user_id=slack_user_id,
            )
        )
        await self.session.flush()

    async def pending_binding(
        self, auth_config_id: UUID
    ) -> SlackPendingAppBindingModel | None:
        result = await self.session.execute(
            select(SlackPendingAppBindingModel).where(
                SlackPendingAppBindingModel.auth_config_id == auth_config_id
            )
        )
        return result.scalar_one_or_none()

    async def drop_pending_binding(self, auth_config_id: UUID) -> None:
        await self.session.execute(
            delete(SlackPendingAppBindingModel).where(
                SlackPendingAppBindingModel.auth_config_id == auth_config_id
            )
        )
