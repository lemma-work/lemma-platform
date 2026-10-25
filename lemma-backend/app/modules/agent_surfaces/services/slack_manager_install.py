"""Making Lemma's own Slack app a workspace's manager.

Authorising Lemma's app makes a connector account like any other. Binding it as
the manager is what turns that into a workspace Lemma's teammates live in: the
workspace becomes the organization's (one organization per workspace), and the
person who authorised it is known in Slack from then on -- Slack's grant names
them (``authed_user``). That is proof because the OAuth callback refuses a
browser that is not the starter's own before it stores any grant: a consent
link somebody was sent never becomes an account to bind.

A workspace held by one organization is freed by removing Lemma's app from it
in Slack (``app_uninstalled``), which deletes the binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
    SlackAppCreationForbiddenError,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (  # noqa: E501
    PROVED_BY_INSTALL,
    SlackManagerRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.slack_manager_messages import stage_welcome
from app.modules.agent_surfaces.services.slack_person_proof import record_proof
from app.modules.connectors.contracts.own_apps import (
    may_manage_installs,
)
from app.modules.connectors.contracts.retirement import retire_accounts
from app.modules.connectors.contracts.surfaces import (
    account_with_secrets,
    auth_config,
)

logger = get_logger(__name__)

_SYSTEM_DEFAULT = "SYSTEM_DEFAULT"


@dataclass(frozen=True)
class ManagedWorkspace:
    team_id: str
    team_name: str | None


@dataclass(frozen=True)
class BoundWorkspace(ManagedWorkspace):
    install_id: UUID
    #: The workspace was not bound before: a first install, or one after Lemma
    #: was removed from it.
    created: bool
    installer_slack_user_id: str | None


def _raw(credentials: dict[str, object]) -> dict[str, object]:
    raw = credentials.get("raw_response")
    return raw if isinstance(raw, dict) else {}


def _nested_id(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    return str(value.get("id") or "") if isinstance(value, dict) else ""


async def _lemma_app_grant(
    uow: SqlAlchemyUnitOfWork, *, user_id: UUID, organization_id: UUID, account_id: UUID
) -> dict[str, object]:
    """The account's OAuth answer, if it is this person's grant of Lemma's own app."""
    expected_app = surface_settings.slack_app_id
    if not expected_app:
        # No app id, no way to tell Lemma's app from anybody's: no manager.
        raise AgentSurfaceValidationError(
            "This Lemma has no Slack app of its own configured, so it can't "
            "manage a workspace."
        )
    found = await account_with_secrets(uow, account_id)
    # "Not yours" and "not there" read the same, as everywhere else accounts are bound.
    if (
        found is None
        or found[0].user_id != user_id
        or found[0].organization_id != organization_id
        or found[0].connector_id != "slack"
    ):
        raise AgentSurfaceValidationError("That Slack account isn't yours to connect.")
    account, credentials = found
    install = (
        await auth_config(uow, account.auth_config_id)
        if account.auth_config_id
        else None
    )
    raw = _raw(credentials)
    if (
        install is None
        or install.config_source != _SYSTEM_DEFAULT
        or str(raw.get("app_id") or "") != expected_app
    ):
        raise AgentSurfaceValidationError(
            "That account is for a Slack app of your own, not Lemma's. A teammate's "
            "own app is added from the teammate; Lemma's app manages them."
        )
    return raw


@dataclass(frozen=True, slots=True)
class SlackGrant:
    """Who a Slack account's grant was, as Slack's OAuth answer says."""

    user_id: UUID
    team_id: str
    #: The Slack user who authorised it (``authed_user``).
    installer: str


async def grant_of(uow: SqlAlchemyUnitOfWork, account_id: UUID) -> SlackGrant | None:
    """The Lemma person a Slack account is, and the workspace and Slack user
    its grant names; None for an account that is not Slack's or is gone."""
    found = await account_with_secrets(uow, account_id)
    if found is None or found[0].connector_id != "slack":
        return None
    account, credentials = found
    raw = _raw(credentials)
    return SlackGrant(
        user_id=account.user_id,
        team_id=_nested_id(raw, "team"),
        installer=_nested_id(raw, "authed_user"),
    )


def _workspace_of(raw: dict[str, object]) -> tuple[str, str | None, str]:
    """``(team_id, team_name, bot_user_id)`` from Slack's OAuth answer."""
    team = raw.get("team")
    team_id = _nested_id(raw, "team")
    team_name = (
        (str(team.get("name") or "") or None) if isinstance(team, dict) else None
    )
    bot_user_id = str(raw.get("bot_user_id") or "")
    if not team_id or not bot_user_id:
        raise AgentSurfaceValidationError(
            "Slack didn't say which workspace that was. Connect Lemma's app again."
        )
    return team_id, team_name, bot_user_id


async def bind_manager(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    organization_id: UUID,
    account_id: UUID,
) -> BoundWorkspace:
    """Bind the workspace this account authorised Lemma's app in to the
    organization, record its installer, and -- for a new binding -- owe the
    installer the manager's welcome. Commits."""
    if not await may_manage_installs(
        uow, user_id=user_id, organization_id=organization_id
    ):
        raise SlackAppCreationForbiddenError()
    raw = await _lemma_app_grant(
        uow, user_id=user_id, organization_id=organization_id, account_id=account_id
    )
    team_id, team_name, bot_user_id = _workspace_of(raw)
    repo = SlackManagerRepository(uow.session)
    claimed = await repo.claim_install(
        team_id=team_id,
        team_name=team_name,
        organization_id=organization_id,
        account_id=account_id,
        bot_user_id=bot_user_id,
        installed_by_user_id=user_id,
    )
    superseded = claimed.superseded_account_id
    if superseded is not None and not await SurfaceRepository(uow).get_bound_to_account(
        superseded, platform=SurfacePlatform.SLACK.value
    ):
        # The workspace's earlier grant of Lemma's app, which only the
        # binding used: its token goes with it. One a pod still answers
        # through (the older arrangement, Lemma's app as a pod's bot) stays.
        await retire_accounts(
            uow.session, connector_id="slack", account_ids=[superseded]
        )
    installer = _nested_id(raw, "authed_user") or None
    if installer:
        await record_proof(
            uow,
            team_id=team_id,
            slack_user_id=installer,
            user_id=user_id,
            proved_by=PROVED_BY_INSTALL,
        )
    if claimed.created:
        # Owed by this binding and staged with it, so it exists exactly when
        # the binding commits; a reauthorisation of the same binding is not
        # a new one and says nothing.
        await stage_welcome(
            uow,
            install_id=claimed.install_id,
            team_id=team_id,
            installer_slack_user_id=installer,
        )
    await uow.commit()
    logger.info(
        "agent_surfaces.slack_manager.bound",
        team_id=team_id,
        organization_id=str(organization_id),
        created=claimed.created,
        superseded=claimed.superseded_account_id is not None,
    )
    return BoundWorkspace(
        team_id=team_id,
        team_name=claimed.team_name,
        install_id=claimed.install_id,
        created=claimed.created,
        installer_slack_user_id=installer,
    )


async def manages_workspace(uow: SqlAlchemyUnitOfWork, team_id: str) -> bool:
    """Whether Lemma's own app manages this Slack workspace for anybody."""
    return (
        await SlackManagerRepository(uow.session).install_for_team(team_id) is not None
    )


async def managed_workspaces(
    uow: SqlAlchemyUnitOfWork, organization_id: UUID
) -> list[ManagedWorkspace]:
    installs = await SlackManagerRepository(uow.session).installs_for_organization(
        organization_id
    )
    return [
        ManagedWorkspace(team_id=row.team_id, team_name=row.team_name)
        for row in installs
    ]
