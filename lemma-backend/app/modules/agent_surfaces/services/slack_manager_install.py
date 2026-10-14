"""Making Lemma's own Slack app a workspace's manager, and proving its people.

Authorising Lemma's app makes a connector account like any other. Binding it as
the manager is what turns that into a workspace Lemma's teammates live in: the
workspace becomes the organization's, and the person who authorised it is
known in Slack from then on -- Slack's OAuth grant names them (``authed_user``),
which is proof the profile email never was.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
    SlackAppCreationForbiddenError,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (
    SlackManagerRepository,
)
from app.modules.agent_surfaces.services.slack_manager_context import (
    verify_person_link,
)
from app.modules.connectors.contracts.own_apps import may_manage_installs
from app.modules.connectors.contracts.surfaces import (
    account_with_secrets,
    auth_config,
)

_SYSTEM_DEFAULT = "SYSTEM_DEFAULT"


@dataclass(frozen=True)
class ManagedWorkspace:
    team_id: str
    team_name: str | None


def _raw(credentials: dict[str, object]) -> dict[str, object]:
    raw = credentials.get("raw_response")
    return raw if isinstance(raw, dict) else {}


def _nested_id(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    return str(value.get("id") or "") if isinstance(value, dict) else ""


async def _lemma_app_grant(
    uow: SqlAlchemyUnitOfWork, *, user_id: UUID, organization_id: UUID, account_id: UUID
) -> tuple[UUID, dict[str, object]]:
    """The account's OAuth grant, if it is this person's grant of Lemma's own app."""
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
    expected_app = surface_settings.slack_app_id
    if (
        install is None
        or install.config_source != _SYSTEM_DEFAULT
        or (expected_app and str(raw.get("app_id") or "") != expected_app)
    ):
        raise AgentSurfaceValidationError(
            "That account is for a Slack app of your own, not Lemma's. A teammate's "
            "own app is added from the teammate; Lemma's app manages them."
        )
    return account.id, raw


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
) -> ManagedWorkspace:
    """Bind the workspace this account authorised Lemma's app in to the organization."""
    if not await may_manage_installs(
        uow, user_id=user_id, organization_id=organization_id
    ):
        raise SlackAppCreationForbiddenError()
    account_id, raw = await _lemma_app_grant(
        uow, user_id=user_id, organization_id=organization_id, account_id=account_id
    )
    team_id, team_name, bot_user_id = _workspace_of(raw)
    repo = SlackManagerRepository(uow.session)
    await repo.save_install(
        team_id=team_id,
        team_name=team_name,
        organization_id=organization_id,
        account_id=account_id,
        bot_user_id=bot_user_id,
        installed_by_user_id=user_id,
    )
    installer = _nested_id(raw, "authed_user")
    if installer:
        await repo.link_person(
            team_id=team_id, slack_user_id=installer, user_id=user_id
        )
    await uow.commit()
    return ManagedWorkspace(team_id=team_id, team_name=team_name)


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


@dataclass(frozen=True)
class PersonLinkPreview:
    """Who a link would connect, shown before anything is connected."""

    team_id: str
    team_name: str | None
    slack_user_name: str | None


async def preview_person_link(
    uow: SqlAlchemyUnitOfWork,
    *,
    team_id: str,
    slack_user_id: str,
    expires: int,
    signature: str,
) -> PersonLinkPreview:
    """Check a link and say whose Slack account it is, connecting nothing.

    The page asks this first and connects only when the person says "that's
    me": a link sent by somebody else would otherwise connect *their* Slack
    account to whoever opened it, signed in, without a word.
    """
    from app.modules.agent_surfaces.platforms.slack.workspace_calls import user_profile
    from app.modules.agent_surfaces.services.slack_manager_context import load_context

    if not verify_person_link(
        team_id=team_id,
        slack_user_id=slack_user_id,
        expires=expires,
        signature=signature,
    ):
        raise AgentSurfaceValidationError(
            "That link has expired. Open Lemma's Home tab in Slack and connect again."
        )
    ctx = await load_context(uow, team_id=team_id, slack_user_id=None)
    if ctx is None:
        raise AgentSurfaceValidationError(
            "Lemma isn't installed in that Slack workspace."
        )
    profile = await user_profile(ctx.credentials, slack_user_id)
    details = profile.get("profile")
    details = details if isinstance(details, dict) else {}
    name = next(
        (
            str(value).strip()
            for value in (
                details.get("real_name"),
                details.get("display_name"),
                profile.get("name"),
            )
            if isinstance(value, str) and value.strip()
        ),
        None,
    )
    return PersonLinkPreview(
        team_id=team_id, team_name=ctx.install.team_name, slack_user_name=name
    )


async def link_person(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    team_id: str,
    slack_user_id: str,
    expires: int,
    signature: str,
) -> ManagedWorkspace:
    """Record that the signed-in person is the Slack user whose Home showed the link."""
    if not verify_person_link(
        team_id=team_id,
        slack_user_id=slack_user_id,
        expires=expires,
        signature=signature,
    ):
        raise AgentSurfaceValidationError(
            "That link has expired. Open Lemma's Home tab in Slack and connect again."
        )
    repo = SlackManagerRepository(uow.session)
    install = await repo.install_for_team(team_id)
    if install is None:
        raise AgentSurfaceValidationError(
            "Lemma isn't installed in that Slack workspace."
        )
    await repo.link_person(
        team_id=team_id, slack_user_id=slack_user_id, user_id=user_id
    )
    await uow.commit()
    return ManagedWorkspace(team_id=team_id, team_name=install.team_name)
