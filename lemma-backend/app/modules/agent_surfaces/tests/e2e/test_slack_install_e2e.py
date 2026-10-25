"""Adding Lemma to Slack from "Add to Slack", end to end.

Through the real install route, the real connect-request callback (only the
token exchange with Slack is faked) and the real work table: the grant is
announced as ``ConnectorAccountConnected``, and ``surface.slack_workspace_
installed`` adopts the workspace only for a grant "Add to Slack" started and
its starter finished in their own browser. Adoption is the manager binding,
which owes the installer one welcome.

Each test has a workspace of its own: a binding is one per workspace.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import facts
from app.modules.agent_surfaces.domain.slack_manager import (
    SLACK_WORKSPACE_INSTALL_PURPOSE,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (  # noqa: E501
    SlackManagerRepository,
)
from app.modules.agent_surfaces.platforms.slack import manager_blocks as views
from app.modules.agent_surfaces.services.slack_install import adopt_workspace
from app.modules.agent_surfaces.tests.e2e.helpers import (
    E2E_SLACK_APP_ID,
    _ensure_connector,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    clean_surface_work,  # noqa: F401  (fixture)
    drain,
    scalar,
)
from app.modules.agent_surfaces.tests.e2e.test_slack_manager_e2e import (
    _manager,
    _member,
    _registry,
    _slack_account,
    _Workspace,
    lemmas_app,  # noqa: F401  (fixture)
)
from app.modules.connectors.contracts.facts import ConnectorAccountConnected
from app.modules.connectors.domain.account import AccountStatus
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.infrastructure.models.connect_request import (
    ConnectRequest,
)
from app.modules.test_support.e2e_base import _create_org, _sign_up_verified_user

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.drives_work,
    pytest.mark.usefixtures("clean_surface_work", "lemmas_app"),
]

CALLBACK = "/connectors/connect-requests/oauth/callback"
INSTALLED_KIND = "surface.slack_workspace_installed"
#: Lemma's Slack app as the deployment configures it: the catalog asks for the
#: bot's scopes and, as ``user_scope``, the connecting person's own.
INSTALLABLE_SLACK: dict[str, object] = {
    "kind": "http",
    "auth_scheme": "OAUTH2",
    "supports_org_custom_oauth": True,
    "oauth2_defaults": {
        "authorization_url": "https://slack.com/oauth/v2/authorize",
        "token_url": "https://slack.com/api/oauth.v2.access",
        "default_scopes": ["chat:write,im:write"],
        "extra_params": {"user_scope": "im:history,search:read"},
    },
    "system_oauth": {
        "client_id_env": "SLACK_CLIENT_ID",
        "client_secret_env": "SLACK_CLIENT_SECRET",
    },
}


@pytest.fixture
async def slack_set_up(db_session: AsyncSession, monkeypatch, fake_slack):
    """This server has Lemma's own Slack app: a client id and secret to send
    people to Slack's consent with, and Slack's token endpoint (the fake's).
    Restored after, for the next test."""
    monkeypatch.setenv("SLACK_CLIENT_ID", "1234.lemma")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "lemma-secret")
    connector = await _ensure_connector(db_session, "slack")
    before = connector.kinds
    defaults = {
        **INSTALLABLE_SLACK["oauth2_defaults"],  # type: ignore[dict-item]
        "token_url": f"{fake_slack.base_url}oauth.v2.access",
    }
    connector.kinds = [{**INSTALLABLE_SLACK, "oauth2_defaults": defaults}]
    fake_slack.oauth_access = None
    await db_session.commit()
    yield
    fake_slack.oauth_access = None
    connector.kinds = before
    await db_session.commit()


def _slack_answers(fake_slack: Any, workspace: _Workspace) -> None:
    """Slack's answer to the token exchange: Lemma's app installed in this
    workspace, by this Slack user. The exchange itself is the real one."""
    fake_slack.oauth_access = {
        "access_token": f"xoxb-{workspace.manager_bot.lower()}",
        "token_type": "bot",
        "app_id": E2E_SLACK_APP_ID,
        "bot_user_id": workspace.manager_bot,
        "team": {"id": workspace.team, "name": "Acme"},
        "authed_user": {"id": workspace.installer},
        "api_base_url": fake_slack.base_url,
    }


async def _add_to_slack(client: AsyncClient, organization_id: str, **headers) -> Any:
    return await client.post(
        "/surface-setup/slack/install",
        json={"organization_id": organization_id},
        headers=headers or None,
    )


async def _slack_comes_back(client: AsyncClient, consent_url: str, **headers) -> str:
    """The browser back from Slack's consent: the redirect the callback answers."""
    state = parse_qs(urlsplit(consent_url).query)["state"][0]
    response = await client.get(
        f"{CALLBACK}?{urlencode({'state': state, 'code': 'c'})}",
        headers=headers or None,
    )
    assert response.status_code == 303, response.text
    return response.headers["location"]


async def _installs_of(db_session: AsyncSession, organization_id: str) -> list:
    db_session.expire_all()
    return list(
        (
            await db_session.scalars(
                select(AuthConfig).where(
                    AuthConfig.organization_id == UUID(organization_id)
                )
            )
        ).all()
    )


def _welcomes(fake_slack: Any) -> list[dict[str, Any]]:
    return [
        message
        for message in fake_slack._store.get_all("SLACK")
        if "Lemma is in" in str(message.get("text"))
    ]


async def _bound_to(team_id: str) -> UUID | None:
    async with async_session_maker() as session:
        row = await SlackManagerRepository(session).install_for_team(team_id)
        return row.organization_id if row is not None else None


# -- starting -------------------------------------------------------------------


async def test_add_to_slack_asks_for_lemmas_bot_alone_on_the_organizations_install(
    authenticated_client, db_session, fixed_test_org, slack_set_up
):
    org_id = fixed_test_org["id"]
    available = await authenticated_client.get("/surface-setup/slack/install")
    assert available.json() == {"available": True}

    response = await _add_to_slack(authenticated_client, org_id)

    assert response.status_code == 200, response.text
    consent = urlsplit(response.json()["authorization_url"])
    query = parse_qs(consent.query)
    assert consent.netloc == "slack.com" and query["client_id"] == ["1234.lemma"]
    assert query["scope"] == ["chat:write,im:write"]
    # The installer's own messages are not what adding the manager needs.
    assert "user_scope" not in query
    (install,) = await _installs_of(db_session, org_id)
    # The same install, by the same name, the web app's Connect makes.
    assert (install.name, install.config_source) == ("slack", "SYSTEM_DEFAULT")
    request = await db_session.scalar(
        select(ConnectRequest).where(ConnectRequest.auth_config_id == install.id)
    )
    assert request is not None
    assert request.attributes["purpose"] == SLACK_WORKSPACE_INSTALL_PURPOSE
    assert request.attributes["return_to"] == "/slack/installed"


async def test_two_starts_at_once_make_one_install_and_neither_fails(
    authenticated_client, db_session, fixed_test_org, slack_set_up
):
    """A double click, or React running the start twice: both miss the
    install, one create is refused for its name, and reads the other's."""
    org_id = fixed_test_org["id"]
    both = await asyncio.gather(
        *(_add_to_slack(authenticated_client, org_id) for _ in range(3))
    )
    assert [r.status_code for r in both] == [200, 200, 200], [r.text for r in both]
    assert len(await _installs_of(db_session, org_id)) == 1


async def test_without_slack_set_up_here_nothing_is_made(
    authenticated_client, db_session, fixed_test_org, monkeypatch
):
    monkeypatch.delenv("SLACK_CLIENT_ID", raising=False)
    monkeypatch.delenv("SLACK_CLIENT_SECRET", raising=False)
    available = await authenticated_client.get("/surface-setup/slack/install")
    assert available.json() == {"available": False}

    response = await _add_to_slack(authenticated_client, fixed_test_org["id"])

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "SLACK_NOT_SET_UP"
    assert await _installs_of(db_session, fixed_test_org["id"]) == []


async def test_a_member_who_cannot_add_installs_is_refused_before_anything_is_made(
    authenticated_client, async_client, db_session, fixed_test_org, slack_set_up
):
    member = await _sign_up_verified_user(async_client)
    await _member(db_session, member, fixed_test_org["id"])
    stranger = await _sign_up_verified_user(async_client)

    for who in (member, stranger):
        response = await _add_to_slack(
            async_client,
            fixed_test_org["id"],
            Authorization=f"Bearer {who['token']}",
        )
        assert response.status_code == 403, response.text
    assert await _installs_of(db_session, fixed_test_org["id"]) == []


# -- coming back ----------------------------------------------------------------


async def test_the_grant_coming_back_adopts_the_workspace_and_welcomes_once(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    slack_set_up,
):
    workspace = _Workspace()
    _slack_answers(fake_slack, workspace)
    org_id = test_pod["organization_id"]

    started = await _add_to_slack(authenticated_client, org_id)
    landed = await _slack_comes_back(
        authenticated_client, started.json()["authorization_url"]
    )
    assert urlsplit(landed).path == "/slack/installed"
    assert parse_qs(urlsplit(landed).query)["connect"] == ["connected"]
    await drain(_registry())

    assert str(await _bound_to(workspace.team)) == org_id
    async with async_session_maker() as session:
        proven = await SlackManagerRepository(session).person(
            workspace.team, workspace.installer
        )
    assert str(proven) == fixed_test_user["id"]
    (welcome,) = _welcomes(fake_slack)
    assert "Lemma is in Acme" in welcome["text"]
    # The one thing worth doing next, as a button: add the first teammate.
    assert views.ADD_ACTION_ID in str(welcome["blocks"])
    assert views.teammate_value(test_pod["id"], test_pod["id"]) in str(
        welcome["blocks"]
    )

    # Somebody who already has Slack connected adds Lemma again: their grant
    # is renewed, not refused as "already connected", and nobody is
    # welcomed twice.
    again = await _add_to_slack(authenticated_client, org_id)
    landed = await _slack_comes_back(
        authenticated_client, again.json()["authorization_url"]
    )
    assert parse_qs(urlsplit(landed).query)["connect"] == ["connected"]
    await drain(_registry())
    assert str(await _bound_to(workspace.team)) == org_id
    assert len(_welcomes(fake_slack)) == 1


async def test_adding_lemma_keeps_the_persons_own_slack_token(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    slack_set_up,
):
    """Their own Slack connect holds a user token; "Add to Slack" asks for the
    bot alone, so its grant has none. Renewing the account must not erase it."""
    workspace = _Workspace()
    org_id = test_pod["organization_id"]
    _slack_answers(fake_slack, workspace)
    fake_slack.oauth_access["authed_user"] = {
        "id": workspace.installer,
        "scope": "im:history,search:read",
        "access_token": "xoxp-their-own",
        "token_type": "user",
    }
    first = await _add_to_slack(authenticated_client, org_id)
    await _slack_comes_back(authenticated_client, first.json()["authorization_url"])

    _slack_answers(fake_slack, workspace)  # bot only: authed_user is just an id
    again = await _add_to_slack(authenticated_client, org_id)
    landed = await _slack_comes_back(
        authenticated_client, again.json()["authorization_url"]
    )
    assert parse_qs(urlsplit(landed).query)["connect"] == ["connected"]

    (install,) = await _installs_of(db_session, org_id)
    async with async_session_maker() as session:
        account = await AccountRepository(
            SqlAlchemyUnitOfWork(session), get_secret_cipher()
        ).get_by_user_and_auth_config(UUID(fixed_test_user["id"]), install.id)
    assert account is not None
    assert account.status == AccountStatus.CONNECTED
    raw = account.credentials.raw_response
    assert raw["authed_user"]["access_token"] == "xoxp-their-own"
    assert raw["authed_user"]["scope"] == "im:history,search:read"
    assert account.credentials.access_token == f"xoxb-{workspace.manager_bot.lower()}"


async def test_cancelling_on_slack_comes_back_to_the_install_page(
    authenticated_client, fixed_test_org, slack_set_up
):
    started = await _add_to_slack(authenticated_client, fixed_test_org["id"])
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]

    response = await authenticated_client.get(
        f"{CALLBACK}?{urlencode({'state': state, 'error': 'access_denied'})}"
    )

    assert response.status_code == 303, response.text
    landed = urlsplit(response.headers["location"])
    # Where the person began, not the app root, with a code the page words.
    assert landed.path == "/slack/installed"
    query = parse_qs(landed.query)
    assert (query["connect"], query["code"]) == (["error"], ["OAUTH_PROVIDER_ERROR"])


async def test_an_ordinary_slack_connect_adopts_nothing_and_says_nothing(
    authenticated_client,
    async_client,
    db_session,
    test_pod,
    fake_slack,
    slack_set_up,
):
    """Lemma's app is also the ordinary Slack connector: connecting it for an
    agent's tools, even by an owner, is never "Add to Slack"."""
    workspace = _Workspace()
    _slack_answers(fake_slack, workspace)
    org_id = test_pod["organization_id"]
    install = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/auth-configs",
        json={"connector_id": "slack", "config_source": "SYSTEM_DEFAULT"},
    )
    assert install.status_code in (200, 201), install.text
    started = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/connect-requests",
        json={"auth_config_id": install.json()["id"]},
    )
    assert started.status_code == 200, started.text

    landed = await _slack_comes_back(
        authenticated_client, started.json()["authorization_url"]
    )
    assert parse_qs(urlsplit(landed).query)["connect"] == ["connected"]
    await drain(_registry())

    assert (
        await scalar(
            "SELECT count(*) FROM work_items WHERE kind = :kind", kind=INSTALLED_KIND
        )
        == 0
    )
    assert await _bound_to(workspace.team) is None
    assert fake_slack._store.get_all("SLACK") == []

    # A member who may not bind reconnecting Slack: nothing is queued at all,
    # so nothing can say "Lemma couldn't join" to them.
    member = await _sign_up_verified_user(async_client)
    await _member(db_session, member, org_id)
    async with async_session_maker() as session:
        queued = await facts.emit(
            SqlAlchemyUnitOfWork(session),
            ConnectorAccountConnected(
                account_id=UUID(parse_qs(urlsplit(landed).query)["account"][0]),
                auth_config_id=UUID(install.json()["id"]),
                connector_id="slack",
                organization_id=UUID(org_id),
                user_id=UUID(member["id"]),
                renewed=True,
            ),
        )
        await session.commit()
    assert queued == 0


async def test_a_consent_link_finished_in_another_session_adopts_nothing(
    authenticated_client,
    async_client,
    db_session,
    test_pod,
    fake_slack,
    slack_set_up,
):
    """The attacker starts "Add to Slack" and sends the consent link to a Slack
    admin, who approves it in their own browser: the grant lands on the
    attacker's account, and adopts nothing and links nobody."""
    workspace = _Workspace()
    _slack_answers(fake_slack, workspace)
    started = await _add_to_slack(authenticated_client, test_pod["organization_id"])
    victim = await _sign_up_verified_user(async_client)

    await _slack_comes_back(
        async_client,
        started.json()["authorization_url"],
        Authorization=f"Bearer {victim['token']}",
    )
    await drain(_registry())

    assert (
        await scalar(
            "SELECT count(*) FROM work_items WHERE kind = :kind", kind=INSTALLED_KIND
        )
        == 0
    )
    assert await _bound_to(workspace.team) is None
    async with async_session_maker() as session:
        linked = await SlackManagerRepository(session).person(
            workspace.team, workspace.installer
        )
    assert linked is None
    assert fake_slack._store.get_all("SLACK") == []


async def test_two_organizations_adding_one_workspace_get_one_binding_and_one_refusal(
    authenticated_client,
    async_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    workspace = _Workspace()
    other = await _sign_up_verified_user(async_client)
    other_org = UUID(str((await _create_org(async_client, other["token"]))["id"]))
    second_installer = f"U{workspace.installer[1:]}2"

    async def grant(user_id: str, organization_id: UUID, installer: str):
        account = await _slack_account(
            db_session,
            user_id=user_id,
            organization_id=organization_id,
            config_source="SYSTEM_DEFAULT",
            raw={
                "app_id": E2E_SLACK_APP_ID,
                "bot_user_id": workspace.manager_bot,
                "team": {"id": workspace.team, "name": "Acme"},
                "authed_user": {"id": installer},
            },
            fake_slack=fake_slack,
            token=f"xoxb-{installer.lower()}",
        )
        return ConnectorAccountConnected(
            account_id=account.id,
            auth_config_id=account.auth_config_id,
            connector_id="slack",
            organization_id=organization_id,
            user_id=UUID(user_id),
            purpose=SLACK_WORKSPACE_INSTALL_PURPOSE,
        )

    ours = await grant(
        fixed_test_user["id"], UUID(test_pod["organization_id"]), workspace.installer
    )
    theirs = await grant(other["id"], other_org, second_installer)

    async def adopt(fact: ConnectorAccountConnected) -> bool:
        async with async_session_maker() as session:
            return (
                await adopt_workspace(SqlAlchemyUnitOfWork(session), fact) is not None
            )

    outcomes = await asyncio.gather(adopt(ours), adopt(theirs))
    assert sorted(outcomes) == [False, True]
    await drain(_registry())

    held_by = await _bound_to(workspace.team)
    loser = second_installer if held_by == ours.organization_id else workspace.installer
    told = [
        message
        for message in fake_slack._store.get_all("SLACK")
        if "couldn't join" in str(message.get("text"))
    ]
    assert len(told) == 1
    assert "another Lemma organization" in told[0]["text"]
    assert loser in str(told[0])
    assert len(_welcomes(fake_slack)) == 1


async def test_binding_from_a_pods_reach_sheet_welcomes_once(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    workspace = _Workspace()
    account, response = await _manager(
        authenticated_client,
        db_session,
        pod=test_pod,
        user=fixed_test_user,
        workspace=workspace,
        fake_slack=fake_slack,
    )
    assert response.status_code == 200, response.text
    # Bound again -- a retried request, a second click: the same binding.
    again = await authenticated_client.put(
        f"/pods/{test_pod['id']}/surface-setup/slack/manager",
        json={"account_id": str(account.id)},
    )
    assert again.status_code == 200, again.text
    await drain(_registry())

    (welcome,) = _welcomes(fake_slack)
    assert "Lemma is in Acme" in welcome["text"]
