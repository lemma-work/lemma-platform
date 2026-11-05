"""Adding Lemma to Slack from "Add to Slack", rather than from a pod.

Somebody clicks "Add to Slack" in Slack's directory or on Lemma's site and
lands on Lemma's ``/slack/install`` page. They sign in the ordinary way -- a
Slack-verified email never opens an account somebody else made -- pick the
organization (or get their first one from main's first-workspace provisioning,
which the page calls), and click once. ``start_slack_install`` then sends the
browser to Slack's consent for Lemma's own app, checked in this order before
anything is made: Lemma's app is configured on this server, and the person
may add the organization's installs.

The connect request carries the purpose ``slack_workspace_install`` and asks
Slack for the bot alone: the installer's own messages are not what installing
the manager needs, and Slack names the installer (``authed_user``) without
any user scope. When the grant comes back, connectors announces it
(``ConnectorAccountConnected``) and ``surface.slack_workspace_installed``
adopts the workspace -- only for that purpose. A consent link somebody was
sent never gets this far: the callback refuses a browser that is not the
starter's before anything is stored. An ordinary Slack connect for tools never
adopts anything.

Adoption is ``bind_manager``, the same binding a pod's reach sheet makes, so
one organization holds a workspace (a second is refused, conflict-tolerant),
and a new binding owes the installer the welcome. A refusal is said in Slack,
by the app that was just installed, to the person who installed it.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceError,
    AgentSurfaceValidationError,
    SlackAppCreationForbiddenError,
)
from app.modules.agent_surfaces.domain.slack_manager import (
    SLACK_WORKSPACE_INSTALL_PURPOSE,
)
from app.modules.agent_surfaces.services.slack_manager_install import (
    BoundWorkspace,
    bind_manager,
    grant_of,
)
from app.modules.agent_surfaces.services.slack_manager_messages import (
    stage_manager_message,
)
from app.modules.connectors.contracts.facts import ConnectorAccountConnected
from app.modules.connectors.contracts.own_apps import (
    ensure_system_install,
    lemma_app_client,
    may_manage_installs,
    start_authorization,
)

logger = get_logger(__name__)

#: Where Slack's consent for Lemma's app lands when started this way. A
#: constant, never the caller's: the callback's redirect is built from it.
INSTALLED_RETURN_PATH = "/slack/installed"
#: Slack's authorize parameter for the installing person's own scopes.
_USER_SCOPES = frozenset({"user_scope"})


class SlackNotSetUpError(AgentSurfaceValidationError):
    """This server has no Slack app of Lemma's own to add."""

    def __init__(self) -> None:
        super().__init__(
            "Slack isn't set up on this Lemma server yet. Whoever runs it can add "
            "Lemma's Slack app."
        )
        self.code = "SLACK_NOT_SET_UP"


class SlackInstallForbiddenError(SlackAppCreationForbiddenError):
    """Adding the organization's installs is an owner's or editor's -- which a
    person who joined a company's organization by its email domain is not."""

    def __init__(self) -> None:
        super().__init__()
        self.message = (
            "Only an owner or editor of this organization can add Lemma to "
            "Slack. Ask one of them to, or pick an organization you manage."
        )


async def install_available(uow: SqlAlchemyUnitOfWork) -> bool:
    """Whether "Add to Slack" can work here: Lemma's own Slack app has a client
    to send people to Slack with, and an app id to know its grants by."""
    return bool(surface_settings.slack_app_id) and (
        await lemma_app_client(uow, "slack") is not None
    )


async def start_slack_install(
    uow: SqlAlchemyUnitOfWork, *, user_id: UUID, organization_id: UUID
) -> str:
    """Slack's consent page for Lemma's own app, for this organization.

    Refuses before making anything: no app here, then no right to add the
    organization's installs (which a non-member does not have either).
    """
    if not await install_available(uow):
        raise SlackNotSetUpError()
    if not await may_manage_installs(
        uow, user_id=user_id, organization_id=organization_id
    ):
        raise SlackInstallForbiddenError()
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
        purpose=SLACK_WORKSPACE_INSTALL_PURPOSE,
        omit_authorize_params=_USER_SCOPES,
    )
    if not url:
        raise SlackNotSetUpError()
    return url


def adopts_workspace(fact: ConnectorAccountConnected) -> bool:
    """A grant "Add to Slack" started.

    Anything else -- an ordinary Slack connect, another flow's purpose -- is
    not one, and is not even queued."""
    return (
        fact.connector_id == "slack" and fact.purpose == SLACK_WORKSPACE_INSTALL_PURPOSE
    )


async def adopt_workspace(
    uow: SqlAlchemyUnitOfWork, fact: ConnectorAccountConnected
) -> BoundWorkspace | None:
    """Make Lemma's app the manager of the workspace this grant names.

    ``bind_manager`` checks everything again (the person's right, Lemma's own
    app) and stages the welcome for a new binding. A refusal
    leaves the account an ordinary account and is said to the installer in
    Slack, once per grant."""
    if not adopts_workspace(fact):
        return None
    try:
        return await bind_manager(
            uow,
            user_id=fact.user_id,
            organization_id=fact.organization_id,
            account_id=fact.account_id,
        )
    except AgentSurfaceError as refused:
        await uow.rollback()
        logger.info(
            "agent_surfaces.slack_manager.install_refused",
            organization_id=str(fact.organization_id),
            reason=type(refused).__name__,
        )
        await _explain(uow, fact, refused)
        return None


async def _explain(
    uow: SqlAlchemyUnitOfWork,
    fact: ConnectorAccountConnected,
    refused: AgentSurfaceError,
) -> None:
    """Owe the installer a DM from the app they just installed, saying why it
    is not staying. Only for this person's own grant of Lemma's app: the grant
    names the Slack user, and nothing else is told anything."""
    grant = await grant_of(uow, fact.account_id)
    if grant is None or grant.user_id != fact.user_id or not grant.installer:
        return
    await stage_manager_message(
        uow,
        organization_id=fact.organization_id,
        team_id=grant.team_id,
        slack_user_id=grant.installer,
        text=f"Lemma couldn't join this workspace: {refused.message}",
        cause=f"install-refused:{fact.account_id}",
        account_id=fact.account_id,
    )
    await uow.commit()
