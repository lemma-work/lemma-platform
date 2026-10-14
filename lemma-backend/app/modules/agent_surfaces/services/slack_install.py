"""Adding Lemma to Slack, starting from Slack's side rather than from a pod.

Someone clicks "Add to Slack" with no Lemma account, or none set up for this.
They sign in to Lemma -- the ordinary way, so a Slack-verified email never
opens an account somebody else made -- and land here, which gives them an
organization and a first teammate if they had neither, and sends them to
Slack's consent for Lemma's own app.

When that grant comes back as a connected account, the workspace it names is
adopted: bound to the organization, Lemma's app becomes its manager, and the
manager says hello in Slack with the teammate to add first. A grant for a
workspace another organization already holds adopts nothing.
"""

from __future__ import annotations

from uuid import UUID

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
    SlackAppCreationForbiddenError,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (
    SlackManagerRepository,
)
from app.modules.agent_surfaces.platforms.slack import manager_blocks as views
from app.modules.agent_surfaces.platforms.slack import workspace_calls as slack
from app.modules.agent_surfaces.services.slack_manager_context import (
    load_context,
    teammates,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.slack_manager_install import (
    bind_manager,
    installer_of,
    lemma_app_grant,
)
from app.modules.connectors.contracts.own_apps import (
    ensure_system_install,
    may_manage_installs,
    start_authorization,
)

logger = get_logger(__name__)

#: Where Slack's consent for Lemma's app lands when started this way.
INSTALLED_RETURN_PATH = "/slack/installed"


async def start_slack_install(uow_factory: UnitOfWorkFactory, *, user_id: UUID) -> str:
    """Slack's consent page for Lemma's own app, for this person's organization."""
    from app.modules.identity.contracts.onboarding import ensure_signed_in_workspace
    from app.modules.identity.contracts.organizations import (
        preferred_organization_membership,
    )

    # Somebody already in an organization installs into that one, checked
    # before anything is made; only a person with none is provisioned one.
    # One organization decides both, so the check and the install can't part.
    async with uow_factory() as uow:
        membership = await preferred_organization_membership(
            uow, user_id=user_id, preferred_organization_id=None
        )
    if membership is not None:
        organization_id = membership[0]
    else:
        workspace = await ensure_signed_in_workspace(uow_factory, user_id=user_id)
        organization_id = workspace.organization_id
    async with uow_factory() as uow:
        if not await may_manage_installs(
            uow, user_id=user_id, organization_id=organization_id
        ):
            raise SlackAppCreationForbiddenError()
        install_id = await ensure_system_install(
            uow, user_id=user_id, organization_id=organization_id, connector_id="slack"
        )
        url = await start_authorization(
            uow,
            user_id=user_id,
            organization_id=organization_id,
            connector_id="slack",
            auth_config_id=install_id,
            return_to=INSTALLED_RETURN_PATH,
        )
    if not url:
        raise AgentSurfaceValidationError(
            "Slack isn't set up on this server yet. Whoever runs it can add Lemma's Slack app."
        )
    return url


async def adopt_workspace(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    organization_id: UUID,
    account_id: UUID,
) -> bool:
    """Make Lemma's app the manager of the workspace this grant names, if nobody has.

    True when the workspace is newly this organization's. Anything that does
    not fit -- not Lemma's app, not this person's to bind, a workspace already
    held -- leaves it as it was: the account stays an account.
    """
    grant = await lemma_app_grant(
        uow, user_id=user_id, organization_id=organization_id, account_id=account_id
    )
    if grant is None:
        return False  # Not Lemma's own app: an ordinary account, nothing to adopt.
    try:
        bound = await bind_manager(
            uow, user_id=user_id, organization_id=organization_id, account_id=account_id
        )
    except DomainError as exc:
        await uow.rollback()
        # Lemma's app was installed and is not being taken on: say so where it
        # was installed, and leave a line for whoever looks into it.
        logger.warning(
            "agent_surfaces.slack_manager.adoption_refused",
            organization_id=str(organization_id),
            reason=str(exc),
        )
        await _explain(
            uow,
            account_id,
            installer_of(grant),
            f"Lemma couldn't join this workspace: {exc}",
        )
        return False
    if not bound.created:
        return False
    await welcome(uow, team_id=bound.team_id, user_id=user_id)
    return True


async def _explain(
    uow: SqlAlchemyUnitOfWork, account_id: UUID, installer: str, text: str
) -> None:
    """A DM from the installed app itself, for when it is not becoming the manager."""
    if not installer:
        return
    credentials = await SurfaceCredentialResolver(uow=uow).for_account(
        account_id, platform=SurfacePlatform.SLACK
    )
    async with connection_released(uow.session):
        channel = await slack.open_dm(credentials, installer)
        if channel:
            await slack.post_message(credentials, channel=channel, text=text)


async def welcome(uow: SqlAlchemyUnitOfWork, *, team_id: str, user_id: UUID) -> None:
    """The manager's first word to whoever installed it: the teammate to add first."""
    installer = await SlackManagerRepository(uow.session).slack_user_for(
        team_id=team_id, user_id=user_id
    )
    if not installer:
        logger.info(
            "agent_surfaces.slack_manager.welcome_skipped",
            team_id=team_id,
            reason="no_installer",
        )
        return
    ctx = await load_context(uow, team_id=team_id, slack_user_id=installer)
    if ctx is None:
        logger.info(
            "agent_surfaces.slack_manager.welcome_skipped",
            team_id=team_id,
            reason="no_install",
        )
        return
    waiting = [mate for mate in await teammates(uow, ctx) if mate.surface is None]
    text, blocks = views.welcome(
        team_name=ctx.install.team_name,
        first=(str(waiting[0].pod_id), waiting[0].name) if waiting else None,
    )
    async with connection_released(uow.session):
        channel = await slack.open_dm(ctx.credentials, installer)
        error = (
            await slack.post_message(
                ctx.credentials, channel=channel, text=text, blocks=blocks
            )
            if channel
            else "dm_unavailable"
        )
    if error:
        logger.warning(
            "agent_surfaces.slack_manager.welcome_failed", team_id=team_id, error=error
        )
        return
    logger.info("agent_surfaces.slack_manager.welcomed", team_id=team_id)
