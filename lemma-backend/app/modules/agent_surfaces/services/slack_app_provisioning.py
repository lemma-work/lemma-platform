"""Lemma making a pod its own Slack app, so the person only clicks Allow.

One Slack app is one bot user: a teammate that answers under its own name, with
its own @-handle and its own DM, needs an app of its own. Making one by hand is
four steps -- copy a manifest, create the app, paste three credentials back,
authorise. With the org's configuration token for the workspace, Lemma does the
first three: it creates the app from the agent's manifest and registers its
credentials as the org's own install, the same install the hand-made path ends
in. What is left is the authorisation, which only a person can give.

The configuration token is saved once per (org, workspace) by someone who may
add the org's installs, since it lets Lemma create apps in that workspace. It is
encrypted like connector credentials, and moves to the secrets vault with them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.errors import (
    SlackAppCreationForbiddenError,
    SlackAppCreationNotSetUpError,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_configuration_token_repository import (
    SlackConfigurationTokenRepository,
    reveal_tokens,
)
from app.modules.agent_surfaces.platforms.slack import app_configuration as slack
from app.modules.agent_surfaces.platforms.slack.manifest import (
    build_slack_app_manifest,
)
from app.modules.connectors.contracts.own_apps import (
    create_own_app_install,
    may_manage_installs,
)

logger = get_logger(__name__)

SLACK_CONNECTOR_ID = "slack"
#: Rotate a little before Slack's 12 hours are up, so a token never expires
#: between being read and being used.
_ROTATE_WITHIN = timedelta(minutes=10)


@dataclass(frozen=True)
class SlackAppWorkspace:
    """A workspace Lemma can create Slack apps in, as a reader may see it."""

    team_id: str
    team_name: str | None
    saved_at: datetime


@dataclass(frozen=True)
class ProvisionedSlackApp:
    app_id: str
    auth_config_id: UUID


@dataclass(frozen=True)
class SlackAppCalls:
    """Slack's side of making an app. A seam, so a test can stand in for Slack."""

    rotate: Callable[[str], Awaitable[slack.RotatedConfigurationToken]] = (
        slack.rotate_configuration_token
    )
    workspace_name: Callable[[str], Awaitable[str | None]] = slack.workspace_name
    create_app: Callable[[str, dict[str, object]], Awaitable[slack.CreatedSlackApp]] = (
        slack.create_app
    )
    delete_app: Callable[[str, str], Awaitable[bool]] = slack.delete_app


@dataclass(frozen=True)
class OrgInstalls:
    """The connectors module's half: who may add installs, and adding one."""

    may_manage: Callable[..., Awaitable[bool]] = may_manage_installs
    create_install: Callable[..., Awaitable[UUID]] = create_own_app_install


class SlackAppProvisioner:
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        calls: SlackAppCalls | None = None,
        installs: OrgInstalls | None = None,
        tokens: SlackConfigurationTokenRepository | None = None,
    ):
        self.uow = uow
        self.slack = calls or SlackAppCalls()
        self.installs = installs or OrgInstalls()
        self.tokens = tokens or SlackConfigurationTokenRepository(uow.session)

    async def workspaces(self, organization_id: UUID) -> list[SlackAppWorkspace]:
        rows = await self.tokens.list_for_organization(organization_id)
        return [
            SlackAppWorkspace(
                team_id=row.team_id, team_name=row.team_name, saved_at=row.created_at
            )
            for row in rows
        ]

    async def save_token(
        self, *, organization_id: UUID, user_id: UUID, refresh_token: str
    ) -> SlackAppWorkspace:
        """Check the refresh token with Slack and keep the pair it rotates to.

        Only the refresh token is asked for: the access token beside it on
        Slack's page expires in hours, and rotating here both proves the refresh
        token works and says which workspace it is for.
        """
        await self._require_install_manager(organization_id, user_id)
        async with connection_released(self.uow.session):
            rotated = await self.slack.rotate(refresh_token)
            team_name = await self.slack.workspace_name(rotated.access_token)
        row = await self.tokens.save(
            organization_id=organization_id,
            token=rotated,
            team_name=team_name,
            saved_by_user_id=user_id,
        )
        await self.uow.commit()
        logger.info(
            "agent_surfaces.slack_app_creation.token_saved",
            organization_id=str(organization_id),
            team_id=rotated.team_id,
        )
        return SlackAppWorkspace(
            team_id=row.team_id, team_name=row.team_name, saved_at=row.created_at
        )

    async def forget(
        self, *, organization_id: UUID, user_id: UUID, team_id: str
    ) -> bool:
        await self._require_install_manager(organization_id, user_id)
        removed = await self.tokens.delete(organization_id, team_id)
        await self.uow.commit()
        return removed

    async def create_pod_app(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        team_id: str,
        app_name: str,
    ) -> ProvisionedSlackApp:
        """Create the app in Slack and register it as the org's own install.

        If the install cannot be made, the app is deleted again: an app whose
        credentials Lemma failed to keep is one nobody can connect, sitting in
        the workspace's app list under the teammate's name.
        """
        await self._require_install_manager(organization_id, user_id)
        access_token = await self._fresh_access_token(organization_id, team_id)
        manifest = build_slack_app_manifest(agent_name=app_name)
        async with connection_released(self.uow.session):
            created = await self.slack.create_app(access_token, manifest)
        try:
            auth_config_id = await self.installs.create_install(
                self.uow,
                user_id=user_id,
                organization_id=organization_id,
                connector_id=SLACK_CONNECTOR_ID,
                # Install names are unique per org; the app id makes this one so
                # however many times a teammate's app is made again.
                name=f"{manifest['display_information']['name']} ({created.app_id})",
                config={
                    "client_id": created.client_id,
                    "client_secret": created.client_secret,
                    "signing_secret": created.signing_secret,
                },
            )
        except DomainError, SQLAlchemyError:
            await self.uow.rollback()
            async with connection_released(self.uow.session):
                deleted = await self.slack.delete_app(access_token, created.app_id)
            logger.warning(
                "agent_surfaces.slack_app_creation.install_failed",
                organization_id=str(organization_id),
                team_id=team_id,
                app_id=created.app_id,
                app_deleted=deleted,
            )
            raise
        logger.info(
            "agent_surfaces.slack_app_creation.created",
            organization_id=str(organization_id),
            team_id=team_id,
            app_id=created.app_id,
        )
        return ProvisionedSlackApp(app_id=created.app_id, auth_config_id=auth_config_id)

    async def _fresh_access_token(self, organization_id: UUID, team_id: str) -> str:
        row = await self.tokens.get(organization_id, team_id)
        if row is None:
            raise SlackAppCreationNotSetUpError(team_id)
        access_token, refresh_token = reveal_tokens(row)
        if row.access_expires_at - datetime.now(timezone.utc) > _ROTATE_WITHIN:
            return access_token
        async with connection_released(self.uow.session):
            rotated = await self.slack.rotate(refresh_token)
        await self.tokens.save(organization_id=organization_id, token=rotated)
        await self.uow.commit()
        return rotated.access_token

    async def _require_install_manager(
        self, organization_id: UUID, user_id: UUID
    ) -> None:
        if not await self.installs.may_manage(
            self.uow, user_id=user_id, organization_id=organization_id
        ):
            raise SlackAppCreationForbiddenError()
