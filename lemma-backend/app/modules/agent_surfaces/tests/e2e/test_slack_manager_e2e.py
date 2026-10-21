"""Lemma's own Slack app as the manager of a workspace's teammates, end to end.

Through the real shared webhook route, signed as Lemma's app, and the real
work table: a manager delivery is answered or queued in the request and run by
``surface.slack_manager``. It answers nobody. It binds a workspace to one
organization, knows its people by Slack's own word (the install, or Sign in
with Slack) rather than a profile email, shows them their teammates, hands a
channel to the one they pick, and finishes adding a teammate once Slack's Allow
comes back as a connectors fact.

Each test has a workspace of its own: a binding is one per workspace.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
from httpx import AsyncClient
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import facts
from app.modules.agent_surfaces.composition import build_app_event_handler
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.events.slack_manager_work import (
    register_slack_manager_work,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (  # noqa: E501
    SlackManagerRepository,
    WorkspaceHeldElsewhereError,
)
from app.modules.agent_surfaces.infrastructure.slack_manager_models import (
    SlackManagerInstallModel,
    SlackPendingAppBindingModel,
    SlackWorkspacePersonModel,
)
from app.modules.agent_surfaces.platforms.slack import manager_blocks as views
from app.modules.agent_surfaces.platforms.slack import workspace_calls
from app.modules.agent_surfaces.platforms.slack.app_configuration import (
    CreatedSlackApp,
    RotatedConfigurationToken,
)
from app.modules.agent_surfaces.services import slack_manager_bots, slack_person_proof
from app.modules.agent_surfaces.services.slack_app_provisioning import SlackAppCalls
from app.modules.agent_surfaces.tests.e2e.helpers import (
    E2E_SLACK_APP_ID,
    _create_agent,
    _ensure_connector,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_slack_signature_headers,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    clean_surface_work,  # noqa: F401  (fixture)
    drain,
    scalar,
    surface_registry,
)
from app.modules.channels.infrastructure.models import (
    ChannelModel,
    ChannelSourceModel,
)
from app.modules.connectors.domain.account import AccountStatus
from app.modules.connectors.contracts.facts import ConnectorAccountConnected
from app.modules.connectors.contracts.own_apps import LemmaAppClient
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connect_request import (
    ConnectRequest,
)
from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)
from app.modules.test_support.e2e_base import _create_org, _sign_up_verified_user

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.drives_work,
    pytest.mark.usefixtures("clean_surface_work", "lemmas_app"),
]

SIGNING_SECRET = "lemma-manager-secret"
OIDC_CLIENT = "lemma-oidc-client"
SLACK_KIND: dict[str, object] = {
    "kind": "http",
    "auth_scheme": "OAUTH2",
    "supports_org_custom_oauth": True,
    "oauth2_defaults": {
        "authorization_url": "https://slack.com/oauth/v2/authorize",
        "token_url": "https://slack.com/api/oauth.v2.access",
    },
}


def _id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:9].upper()}"


@dataclass
class _Workspace:
    """One Slack workspace no other test uses."""

    team: str = field(default_factory=lambda: _id("T"))
    manager_bot: str = field(default_factory=lambda: _id("U"))
    installer: str = field(default_factory=lambda: _id("U"))
    channel: str = field(default_factory=lambda: _id("C"))


@pytest.fixture
def lemmas_app(monkeypatch):
    """The deployment's own Slack app: the e2e accounts' app, signing with this."""
    monkeypatch.setattr(surface_settings, "slack_app_id", E2E_SLACK_APP_ID)
    monkeypatch.setattr(surface_settings, "slack_signing_secret", SIGNING_SECRET)


def _registry():
    registry = surface_registry()
    register_slack_manager_work(registry)
    return registry


async def _slack_account(
    db_session: AsyncSession,
    *,
    user_id: str,
    organization_id: UUID,
    config_source: str,
    raw: dict[str, Any],
    fake_slack: Any,
    token: str,
) -> Account:
    """A Slack account on an install of its own: Lemma's app and each teammate's
    app are separate installs, and a person holds an account on each."""
    await _ensure_connector(db_session, "slack")
    install = AuthConfig(
        organization_id=organization_id,
        connector_id="slack",
        name=f"slack {uuid4().hex[:8]}",
        kind="http",
        config_source=config_source,
        status="ACTIVE",
    )
    db_session.add(install)
    await db_session.flush()
    account = Account(
        user_id=UUID(user_id),
        organization_id=organization_id,
        auth_config_id=install.id,
        connector_id="slack",
        provider_account_id=f"e2e-slack-{uuid4().hex[:8]}",
        credentials={
            "access_token": token,
            "api_base_url": fake_slack.base_url,
            "raw_response": {**raw, "api_base_url": fake_slack.base_url},
        },
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)
    return account


async def _granted(
    db_session: AsyncSession, account: Account, *, browser_bound: bool = True
) -> None:
    """The connect request Slack's Allow finished, as the callback records it."""
    db_session.add(
        ConnectRequest(
            user_id=account.user_id,
            organization_id=account.organization_id,
            auth_config_id=account.auth_config_id,
            connector_id="slack",
            status="SUCCESS",
            attributes={
                "state": uuid4().hex,
                "granted_account_id": str(account.id),
                "browser_bound": browser_bound,
            },
        )
    )
    await db_session.commit()


async def _manager(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    pod: dict,
    user: dict,
    workspace: _Workspace,
    fake_slack: Any,
    browser_bound: bool = True,
):
    account = await _slack_account(
        db_session,
        user_id=user["id"],
        organization_id=UUID(pod["organization_id"]),
        config_source="SYSTEM_DEFAULT",
        raw={
            "app_id": E2E_SLACK_APP_ID,
            "bot_user_id": workspace.manager_bot,
            "team": {"id": workspace.team, "name": "Acme"},
            "authed_user": {"id": workspace.installer},
        },
        fake_slack=fake_slack,
        token=f"xoxb-{workspace.manager_bot.lower()}",
    )
    await _granted(db_session, account, browser_bound=browser_bound)
    response = await client.put(
        f"/pods/{pod['id']}/surface-setup/slack/manager",
        json={"account_id": str(account.id)},
    )
    return account, response


async def _teammate(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    pod: dict,
    user: dict,
    workspace: _Workspace,
    fake_slack: Any,
) -> tuple[dict, str]:
    """A teammate with its own app already in the workspace: (surface, bot)."""
    bot = _id("U")
    account = await _slack_account(
        db_session,
        user_id=user["id"],
        organization_id=UUID(pod["organization_id"]),
        config_source="ORG_CUSTOM",
        raw={"app_id": _id("A"), "bot_user_id": bot, "team": {"id": workspace.team}},
        fake_slack=fake_slack,
        token=f"xoxb-{bot.lower()}",
    )
    agent = await _create_agent(client, pod["id"])
    response = await client.post(
        f"/pods/{pod['id']}/surfaces",
        json={
            "platform": "SLACK",
            "account_id": str(account.id),
            "name": f"slack-{bot.lower()}",
            "default_agent_name": agent["name"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json(), bot


async def _say(
    client: AsyncClient,
    workspace: _Workspace,
    *,
    event: dict[str, Any] | None = None,
    interaction: dict[str, Any] | None = None,
) -> Any:
    """One delivery from Lemma's app, POSTed and signed the way Slack sends it."""
    if event is not None:
        body = {
            "type": "event_callback",
            "team_id": workspace.team,
            "api_app_id": E2E_SLACK_APP_ID,
            "event_id": _id("Ev"),
            "authorizations": [
                {"team_id": workspace.team, "user_id": workspace.manager_bot}
            ],
            "event": event,
        }
        raw = json.dumps(body).encode()
        content_type = "application/json"
    else:
        assert interaction is not None
        body = {
            "team": {"id": workspace.team},
            "api_app_id": E2E_SLACK_APP_ID,
            **interaction,
        }
        raw = urlencode({"payload": json.dumps(body)}).encode()
        content_type = "application/x-www-form-urlencoded"
    headers = build_slack_signature_headers(raw_body=raw, signing_secret=SIGNING_SECRET)
    headers["Content-Type"] = content_type
    response = await client.post(
        "/surfaces/webhooks/slack", content=raw, headers=headers
    )
    assert response.status_code == 200, response.text
    return response


async def _manager_items() -> int:
    return int(
        await scalar(
            "SELECT count(*) FROM work_items WHERE kind = 'surface.slack_manager'"
        )
    )


async def _nothing_reached_the_inbound_pipeline() -> bool:
    inbound = await scalar("SELECT count(*) FROM surface_inbound_messages")
    updates = await scalar(
        "SELECT count(*) FROM work_items WHERE kind IN "
        "('surface.platform_update', 'surface.ingest')"
    )
    return inbound == 0 and updates == 0


def _home_cannot_publish(monkeypatch) -> list[str]:
    """Slack unreachable for the Home tab alone: a transport error, which
    ``publish_home`` does not turn into a refusal. Returns who it was for."""
    attempted: list[str] = []

    async def unreachable(credentials, *, user_id, view):
        attempted.append(user_id)
        raise httpx.ConnectError("slack.com unreachable")

    monkeypatch.setattr(workspace_calls, "publish_home", unreachable)
    return attempted


def _views(fake_slack: Any, bucket: str = "SLACK_VIEWS_PUBLISH") -> list[str]:
    return [json.dumps(message) for message in fake_slack._store.get_all(bucket)]


async def _bound(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
) -> _Workspace:
    workspace = _Workspace()
    _, response = await _manager(
        authenticated_client,
        db_session,
        pod=test_pod,
        user=fixed_test_user,
        workspace=workspace,
        fake_slack=fake_slack,
    )
    assert response.status_code == 200, response.text
    return workspace


# -- binding the workspace ----------------------------------------------------


async def test_binding_makes_the_workspace_the_organizations_and_proves_the_installer(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    workspace = _Workspace()
    _, response = await _manager(
        authenticated_client,
        db_session,
        pod=test_pod,
        user=fixed_test_user,
        workspace=workspace,
        fake_slack=fake_slack,
    )
    assert response.json() == {"team_id": workspace.team, "team_name": "Acme"}
    listed = await authenticated_client.get(
        f"/pods/{test_pod['id']}/surface-setup/slack/manager"
    )
    assert workspace.team in [item["team_id"] for item in listed.json()["items"]]

    db_session.expire_all()
    proof = await db_session.scalar(
        select(SlackWorkspacePersonModel).where(
            SlackWorkspacePersonModel.team_id == workspace.team
        )
    )
    assert proof is not None
    assert (proof.slack_user_id, str(proof.user_id), proof.proved_by) == (
        workspace.installer,
        fixed_test_user["id"],
        "INSTALL",
    )
    # Through to the identity cache, so every reader of it knows them too.
    cached = await scalar(
        "SELECT resolved_user_id FROM agent_surface_external_users "
        "WHERE platform = 'SLACK' AND tenant_id = :team AND external_user_id = :user",
        team=workspace.team,
        user=workspace.installer,
    )
    assert str(cached) == fixed_test_user["id"]


async def test_a_grant_finished_in_somebody_elses_browser_binds_nothing(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    """A consent link sent to someone: their Allow, the sender's account."""
    workspace = _Workspace()
    _, response = await _manager(
        authenticated_client,
        db_session,
        pod=test_pod,
        user=fixed_test_user,
        workspace=workspace,
        fake_slack=fake_slack,
        browser_bound=False,
    )
    assert response.status_code == 422, response.text
    assert "browser" in response.text
    async with async_session_maker() as session:
        assert (
            await SlackManagerRepository(session).install_for_team(workspace.team)
            is None
        )
        assert (
            await SlackManagerRepository(session).person(
                workspace.team, workspace.installer
            )
            is None
        )


async def test_without_an_app_of_its_own_this_lemma_manages_nothing(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack, monkeypatch
):
    monkeypatch.setattr(surface_settings, "slack_app_id", None)
    _, response = await _manager(
        authenticated_client,
        db_session,
        pod=test_pod,
        user=fixed_test_user,
        workspace=_Workspace(),
        fake_slack=fake_slack,
    )
    assert response.status_code == 422, response.text


async def test_a_second_organization_is_refused_not_failed_even_at_once(
    authenticated_client,
    async_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    """One organization per workspace, by a conflict-tolerant insert."""
    workspace = _Workspace()
    other = await _sign_up_verified_user(async_client)
    other_org = UUID(str((await _create_org(async_client, other["token"]))["id"]))
    ours = await _slack_account(
        db_session,
        user_id=fixed_test_user["id"],
        organization_id=UUID(test_pod["organization_id"]),
        config_source="SYSTEM_DEFAULT",
        raw={},
        fake_slack=fake_slack,
        token="xoxb-a",
    )
    theirs = await _slack_account(
        db_session,
        user_id=other["id"],
        organization_id=other_org,
        config_source="SYSTEM_DEFAULT",
        raw={},
        fake_slack=fake_slack,
        token="xoxb-b",
    )

    async def claim(organization_id: UUID, account_id: UUID) -> str:
        async with async_session_maker() as session:
            try:
                await SlackManagerRepository(session).claim_install(
                    team_id=workspace.team,
                    team_name=None,
                    organization_id=organization_id,
                    account_id=account_id,
                    bot_user_id=workspace.manager_bot,
                    installed_by_user_id=None,
                )
            except WorkspaceHeldElsewhereError:
                return "refused"
            await session.commit()
            return "bound"

    import asyncio

    outcomes = await asyncio.gather(
        claim(UUID(test_pod["organization_id"]), ours.id), claim(other_org, theirs.id)
    )
    assert sorted(outcomes) == ["bound", "refused"]


# -- its own lane ---------------------------------------------------------------


async def test_a_home_opened_is_queued_as_ids_and_shows_a_stranger_how_to_connect(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    stranger = _id("U")
    await _say(
        authenticated_client,
        workspace,
        event={"type": "app_home_opened", "user": stranger},
    )
    assert await _manager_items() == 1
    assert await _nothing_reached_the_inbound_pipeline()

    await drain(_registry())

    home = _views(fake_slack)[-1]
    assert "Connect" in home
    assert f"/slack/connect?team={workspace.team}&user={stranger}" in home


async def test_the_installers_home_is_the_roster_one_row_per_agent(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    """A pod with two agents in Slack lists both, and its own agent's Add."""
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    _, first = await _teammate(
        authenticated_client,
        db_session,
        pod=test_pod,
        user=fixed_test_user,
        workspace=workspace,
        fake_slack=fake_slack,
    )
    _, second = await _teammate(
        authenticated_client,
        db_session,
        pod=test_pod,
        user=fixed_test_user,
        workspace=workspace,
        fake_slack=fake_slack,
    )
    await _say(
        authenticated_client,
        workspace,
        event={"type": "app_home_opened", "user": workspace.installer},
    )
    await drain(_registry())

    home = _views(fake_slack)[-1]
    assert f"<@{first}>" in home and f"<@{second}>" in home
    assert views.teammate_value(test_pod["id"], test_pod["id"]) in home


async def test_its_own_leaving_never_archives_a_teammates_mirror(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    """The manager's channel_left must never read as the teammate's bot leaving."""
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    surface, _ = await _teammate(
        authenticated_client,
        db_session,
        pod=test_pod,
        user=fixed_test_user,
        workspace=workspace,
        fake_slack=fake_slack,
    )
    await _hand_over(authenticated_client, workspace, surface)
    await drain(_registry())
    assert await _mirrors(db_session, workspace) == [(False, False)]

    await _say(
        authenticated_client,
        workspace,
        event={"type": "channel_left", "channel": workspace.channel},
    )
    assert await _nothing_reached_the_inbound_pipeline()
    await drain(_registry())
    assert await _mirrors(db_session, workspace) == [(False, False)]


async def test_a_dm_to_the_manager_is_answered_and_stored_nowhere(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    await _say(
        authenticated_client,
        workspace,
        event={
            "type": "message",
            "channel_type": "im",
            "user": workspace.installer,
            "channel": "D1",
            "text": "hello?",
        },
    )
    assert await _nothing_reached_the_inbound_pipeline()
    await drain(_registry())
    sent = fake_slack._store.get_all("SLACK")
    assert any("I don't answer questions myself" in str(m) for m in sent)


async def test_removing_lemma_frees_the_workspace_and_retires_its_token(
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
    assert response.status_code == 200
    await _say(authenticated_client, workspace, event={"type": "app_uninstalled"})
    assert await _nothing_reached_the_inbound_pipeline()
    await drain(_registry())

    async with async_session_maker() as session:
        assert (
            await SlackManagerRepository(session).install_for_team(workspace.team)
            is None
        )
        status = await session.scalar(
            select(Account.status).where(Account.id == account.id)
        )
    # Retired so a reconnect can bring it back, not merely "not connected".
    assert AccountStatus(status) is AccountStatus.REAUTH_REQUIRED


# -- handing a channel over -------------------------------------------------------


async def _hand_over(client: AsyncClient, workspace: _Workspace, surface: dict) -> None:
    await _say(
        client,
        workspace,
        interaction={
            "type": "block_actions",
            "user": {"id": workspace.installer},
            "container": {"type": "message", "channel_id": workspace.channel},
            "response_url": "",
            "actions": [
                {
                    "action_id": f"{views.JOIN_ACTION_ID}:0",
                    "value": json.dumps(
                        {"channel": workspace.channel, "surface": surface["id"]}
                    ),
                }
            ],
        },
    )


async def _mirrors(
    db_session: AsyncSession, workspace: _Workspace
) -> list[tuple[bool, bool]]:
    """``(is_private, archived)`` for each mirror of the workspace's channel."""
    db_session.expire_all()
    rows = await db_session.execute(
        select(ChannelModel.visibility, ChannelModel.archived_at)
        .join(ChannelSourceModel, ChannelSourceModel.channel_id == ChannelModel.id)
        .where(ChannelSourceModel.external_channel_id == workspace.channel)
    )
    return [
        (str(visibility).lower() == "private", archived is not None)
        for visibility, archived in rows
    ]


@pytest.mark.parametrize(
    ("slack_says", "mirrored_private"),
    [("public", False), ("private", True), ("nothing", True)],
)
async def test_a_hand_off_invites_the_teammate_with_main_hello_and_slacks_privacy(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    slack_says,
    mirrored_private,
):
    """Public stays public; private, or no answer, is mirrored private."""
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    if slack_says == "private":
        fake_slack.private_channels.add(workspace.channel)
    elif slack_says == "nothing":
        fake_slack.unknown_channels.add(workspace.channel)
    surface, bot = await _teammate(
        authenticated_client,
        db_session,
        pod=test_pod,
        user=fixed_test_user,
        workspace=workspace,
        fake_slack=fake_slack,
    )
    await _hand_over(authenticated_client, workspace, surface)
    await drain(_registry())

    invites = fake_slack._store.get_all("SLACK_INVITE")
    assert [(i["channel"], i["users"]) for i in invites] == [(workspace.channel, bot)]
    # Invited by the manager, public or private: no channels:join needed.
    assert workspace.manager_bot.lower() in invites[0]["token"]
    assert await _mirrors(db_session, workspace) == [(mirrored_private, False)]
    hello = [m for m in fake_slack._store.get_all("SLACK") if "keeps" in str(m)]
    assert len(hello) == 1 and hello[0]["channel"] == workspace.channel
    left = fake_slack._store.get_all("SLACK_LEAVE")
    assert [leave["channel"] for leave in left] == [workspace.channel]


async def test_a_refused_invite_takes_the_channel_back_and_steps_out(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    surface, _ = await _teammate(
        authenticated_client,
        db_session,
        pod=test_pod,
        user=fixed_test_user,
        workspace=workspace,
        fake_slack=fake_slack,
    )
    fake_slack.invite_error = "cant_invite"
    await _hand_over(authenticated_client, workspace, surface)
    await drain(_registry())

    routes = await authenticated_client.get(
        f"/pods/{test_pod['id']}/surfaces/{surface['name']}"
    )
    assert workspace.channel not in routes.text
    assert await _mirrors(db_session, workspace) == [(False, True)]
    assert not [m for m in fake_slack._store.get_all("SLACK") if "keeps" in str(m)]
    assert fake_slack._store.get_all("SLACK_LEAVE")


async def test_a_join_button_for_another_channel_is_refused(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    surface, _ = await _teammate(
        authenticated_client,
        db_session,
        pod=test_pod,
        user=fixed_test_user,
        workspace=workspace,
        fake_slack=fake_slack,
    )
    await _say(
        authenticated_client,
        workspace,
        interaction={
            "type": "block_actions",
            "user": {"id": workspace.installer},
            "container": {"type": "message", "channel_id": _id("C")},
            "actions": [
                {
                    "action_id": f"{views.JOIN_ACTION_ID}:0",
                    "value": json.dumps(
                        {"channel": workspace.channel, "surface": surface["id"]}
                    ),
                }
            ],
        },
    )
    await drain(_registry())
    assert not fake_slack._store.get_all("SLACK_INVITE")
    assert await _mirrors(db_session, workspace) == []


async def test_the_teammate_invited_by_the_manager_asks_nobody_to_set_it_up(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    """Its own member_joined_channel names the manager as inviter: no prompt."""
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    surface, bot = await _teammate(
        authenticated_client,
        db_session,
        pod=test_pod,
        user=fixed_test_user,
        workspace=workspace,
        fake_slack=fake_slack,
    )
    joined = SurfacePlatformWebhookIngress(
        source="slack",
        payload={
            "type": "event_callback",
            "team_id": workspace.team,
            "api_app_id": "A-TEAMMATE",
            "authorizations": [{"team_id": workspace.team, "user_id": bot}],
            "event": {
                "type": "member_joined_channel",
                "user": bot,
                "inviter": workspace.manager_bot,
                "channel": _id("C"),
            },
        },
        receiver_surface_ids=[UUID(surface["id"])],
    )
    async with async_session_maker() as session:
        handled = await build_app_event_handler(
            SqlAlchemyUnitOfWork(session)
        ).try_handle_lifecycle(joined)
    assert handled
    assert not fake_slack._store.get_all("SLACK_EPHEMERAL")


# -- Add to Slack, from Slack ---------------------------------------------------


@dataclass
class _FakeSlackApps:
    """Slack's app configuration API: a refresh token works once."""

    team: str
    valid: set[str] = field(default_factory=lambda: {"xoxe-1-pasted"})
    created: list[str] = field(default_factory=list)

    async def rotate(self, refresh_token: SecretStr) -> RotatedConfigurationToken:
        value = refresh_token.get_secret_value()
        assert value in self.valid
        self.valid.discard(value)
        fresh = f"xoxe-1-next-{len(self.valid)}"
        self.valid.add(fresh)
        return RotatedConfigurationToken(
            access_token=SecretStr("xoxe.xoxp-access"),
            refresh_token=SecretStr(fresh),
            team_id=self.team,
            slack_user_id="U0ADMIN",
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        )

    async def workspace_name(self, access_token: SecretStr) -> str | None:
        return "Acme"

    async def create_app(
        self, access_token: SecretStr, manifest: dict[str, object]
    ) -> CreatedSlackApp:
        app_id = f"A0MGR{len(self.created) + 1}"
        self.created.append(app_id)
        return CreatedSlackApp(
            app_id=app_id,
            client_id=f"cid-{app_id}",
            client_secret=SecretStr(f"csecret-{app_id}"),
            signing_secret=SecretStr(f"ssecret-{app_id}"),
        )

    async def delete_app(self, access_token: SecretStr, app_id: str) -> bool:
        return True

    def calls(self) -> SlackAppCalls:
        return SlackAppCalls(
            rotate=self.rotate,
            workspace_name=self.workspace_name,
            create_app=self.create_app,
            delete_app=self.delete_app,
        )


def _submitted(workspace: _Workspace, pod_id: str, *, nonce: str) -> dict[str, Any]:
    return {
        "type": "view_submission",
        "user": {"id": workspace.installer},
        "view": {
            "id": "V-ADD",
            # Two submissions of one modal differ in their hash.
            "hash": nonce,
            "callback_id": views.ADD_VIEW_CALLBACK_ID,
            "private_metadata": json.dumps(views.modal_metadata(pod_id, pod_id)),
            "state": {
                "values": {
                    views.TOKEN_BLOCK_ID: {
                        views.INPUT_ACTION_ID: {"value": "xoxe-1-pasted"}
                    }
                }
            },
        },
    }


async def test_add_to_slack_from_slack_makes_one_app_and_queues_no_secret(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack, monkeypatch
):
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    connector = await _ensure_connector(db_session, "slack")
    connector.kinds = [SLACK_KIND]
    await db_session.commit()
    apps = _FakeSlackApps(team=workspace.team)
    monkeypatch.setattr(slack_manager_bots, "slack_app_calls", apps.calls)

    first = await _say(
        authenticated_client,
        workspace,
        interaction=_submitted(workspace, test_pod["id"], nonce="h1"),
    )
    assert first.json()["response_action"] == "update"
    # A double submit of the same modal, before the first is done.
    await _say(
        authenticated_client,
        workspace,
        interaction=_submitted(workspace, test_pod["id"], nonce="h2"),
    )
    queued = await scalar(
        "SELECT string_agg(payload::text, ' ') FROM work_items "
        "WHERE kind = 'surface.slack_manager'"
    )
    assert "xoxe-1-pasted" not in str(queued)
    assert await _nothing_reached_the_inbound_pipeline()

    await drain(_registry(), due=True)

    assert apps.created == ["A0MGR1"]
    for table, column in (
        ("work_items", "payload::text"),
        ("connect_requests", "attributes::text"),
    ):
        found = await scalar(
            f"SELECT count(*) FROM {table} WHERE {column} LIKE '%xoxe-1-pasted%'"  # noqa: S608
        )
        assert found == 0, table
    allow = _views(fake_slack, "SLACK_VIEWS_UPDATE")[-1]
    assert "Allow" in allow
    db_session.expire_all()
    pending = await db_session.scalar(
        select(SlackPendingAppBindingModel).where(
            SlackPendingAppBindingModel.team_id == workspace.team
        )
    )
    assert pending is not None
    assert (pending.source, pending.slack_user_id) == ("SLACK", workspace.installer)
    purpose = await scalar(
        "SELECT attributes->>'purpose' FROM connect_requests "
        "WHERE auth_config_id = :install ORDER BY created_at DESC LIMIT 1",
        install=pending.auth_config_id,
    )
    assert purpose == "slack_teammate_app"


async def test_a_home_that_fails_to_refresh_leaves_the_allow_on_screen(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack, monkeypatch
):
    """The add is done when the Home refresh after it fails. Retrying the item
    would take the spent form again and put "took too long" over the Allow."""
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    connector = await _ensure_connector(db_session, "slack")
    connector.kinds = [SLACK_KIND]
    await db_session.commit()
    apps = _FakeSlackApps(team=workspace.team)
    monkeypatch.setattr(slack_manager_bots, "slack_app_calls", apps.calls)
    attempted = _home_cannot_publish(monkeypatch)

    await _say(
        authenticated_client,
        workspace,
        interaction=_submitted(workspace, test_pod["id"], nonce="h1"),
    )
    await drain(_registry(), due=True)
    # Anything left to retry runs now, rather than after its backoff.
    await drain(_registry(), due=True)

    assert attempted, "the Home refresh was not attempted"
    assert apps.created == ["A0MGR1"]
    shown = _views(fake_slack, "SLACK_VIEWS_UPDATE")
    assert "Allow" in shown[-1]
    assert not any("took too long" in view for view in shown)
    # Finished, not waiting to try again.
    left = await scalar(
        "SELECT count(*) FROM work_items WHERE kind = 'surface.slack_manager'"
    )
    assert left == 0


async def _allowed(
    db_session: AsyncSession,
    *,
    pending: SlackPendingAppBindingModel,
    account: Account,
    purpose: str | None,
) -> int:
    """Connectors announcing the grant, in a transaction of its own."""
    async with async_session_maker() as session:
        added = await facts.emit(
            SqlAlchemyUnitOfWork(session),
            ConnectorAccountConnected(
                account_id=account.id,
                auth_config_id=pending.auth_config_id,
                connector_id="slack",
                organization_id=pending.organization_id,
                user_id=pending.requested_by_user_id,
                purpose=purpose,
            ),
        )
        await session.commit()
    return added


async def _awaiting_allow(
    db_session: AsyncSession,
    *,
    workspace: _Workspace,
    test_pod: dict,
    fixed_test_user: dict,
    fake_slack: Any,
) -> tuple[str, Account, SlackPendingAppBindingModel]:
    """A teammate's app added from Slack, whose Allow has just made an account:
    ``(its bot, the account, the pending binding)``."""
    bot = _id("U")
    account = await _slack_account(
        db_session,
        user_id=fixed_test_user["id"],
        organization_id=UUID(test_pod["organization_id"]),
        config_source="ORG_CUSTOM",
        raw={"app_id": "A0ALLOWED", "bot_user_id": bot, "team": {"id": workspace.team}},
        fake_slack=fake_slack,
        token=f"xoxb-{bot.lower()}",
    )
    pending = SlackPendingAppBindingModel(
        organization_id=UUID(test_pod["organization_id"]),
        pod_id=UUID(test_pod["id"]),
        agent_id=UUID(test_pod["id"]),
        auth_config_id=account.auth_config_id,
        team_id=workspace.team,
        app_id="A0ALLOWED",
        requested_by_user_id=UUID(fixed_test_user["id"]),
        slack_user_id=workspace.installer,
        source="SLACK",
    )
    db_session.add(pending)
    await db_session.commit()
    return bot, account, pending


async def test_the_allow_coming_back_makes_the_surface_once_and_says_so(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    bot, account, pending = await _awaiting_allow(
        db_session,
        workspace=workspace,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_slack=fake_slack,
    )

    # An ordinary Slack connect for tools is never this.
    assert (
        await _allowed(db_session, pending=pending, account=account, purpose=None) == 0
    )
    assert (
        await _allowed(
            db_session, pending=pending, account=account, purpose="slack_teammate_app"
        )
        == 1
    )
    await drain(_registry())
    # Delivered again, after the first ran: nothing left to do.
    await _allowed(
        db_session, pending=pending, account=account, purpose="slack_teammate_app"
    )
    await drain(_registry())

    surfaces = await scalar(
        "SELECT count(*) FROM agent_surfaces WHERE account_id = :account",
        account=account.id,
    )
    assert surfaces == 1
    left = await scalar(
        "SELECT count(*) FROM slack_pending_app_bindings WHERE id = :id", id=pending.id
    )
    assert left == 0
    told = [m for m in fake_slack._store.get_all("SLACK") if f"<@{bot}>" in str(m)]
    assert len(told) == 1


async def test_a_home_that_fails_to_refresh_never_sends_the_dm_again(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack, monkeypatch
):
    """ "X is in Slack now" is out before the Home refresh fails; a retry of the
    item would send it a second time."""
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    bot, account, pending = await _awaiting_allow(
        db_session,
        workspace=workspace,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_slack=fake_slack,
    )
    attempted = _home_cannot_publish(monkeypatch)
    await _allowed(
        db_session, pending=pending, account=account, purpose="slack_teammate_app"
    )

    await drain(_registry())
    # Anything left to retry runs now, rather than after its backoff.
    await drain(_registry(), due=True)
    await drain(_registry(), due=True)

    assert workspace.installer in attempted
    told = [m for m in fake_slack._store.get_all("SLACK") if f"<@{bot}>" in str(m)]
    assert len(told) == 1
    left = await scalar(
        "SELECT count(*) FROM work_items WHERE kind = 'surface.slack_manager_message'"
    )
    assert left == 0


# -- Sign in with Slack -----------------------------------------------------------


@dataclass
class _SlackSignIn:
    """Slack's OpenID side: signs whatever identity the test says signed in."""

    key: rsa.RSAPrivateKey = field(
        default_factory=lambda: rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
    )
    signed_in_as: tuple[str, str] = ("", "")
    nonce: str = ""

    async def exchange(self, client: LemmaAppClient, code: str, redirect: str) -> str:
        team, user = self.signed_in_as
        now = int(time.time())
        return jwt.encode(
            {
                "iss": "https://slack.com",
                "aud": client.client_id,
                "sub": user,
                "iat": now,
                "exp": now + 300,
                "nonce": self.nonce,
                "https://slack.com/user_id": user,
                "https://slack.com/team_id": team,
            },
            self.key,
            algorithm="RS256",
            headers={"kid": "k1"},
        )

    async def signing_keys(self) -> dict[str, Any]:
        public = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        return {"keys": [{**public, "kid": "k1"}]}


@pytest.fixture
def slack_sign_in(monkeypatch) -> _SlackSignIn:
    slack = _SlackSignIn()

    async def lemma_app(uow, connector_id):
        return LemmaAppClient(client_id=OIDC_CLIENT, client_secret=SecretStr("s"))

    monkeypatch.setattr(slack_person_proof, "lemma_app_client", lemma_app)
    monkeypatch.setattr(
        slack_person_proof,
        "SLACK_OIDC",
        slack_person_proof.SlackOidcCalls(
            exchange=slack.exchange, signing_keys=slack.signing_keys
        ),
    )
    return slack


async def _start(client: AsyncClient, workspace: _Workspace, user: str, **headers):
    return await client.post(
        "/surface-setup/slack/people/sign-in",
        json={"team_id": workspace.team, "slack_user_id": user},
        headers=headers or None,
    )


def _state_and_nonce(url: str) -> tuple[str, str]:
    query = parse_qs(urlsplit(url).query)
    assert query["team"] == [query["team"][0]]
    return query["state"][0], query["nonce"][0]


async def _callback(client: AsyncClient, state: str, **headers) -> str:
    response = await client.get(
        f"{slack_person_proof.CALLBACK_PATH}?{urlencode({'state': state, 'code': 'c'})}",
        headers=headers or None,
    )
    assert response.status_code == 303, response.text
    return response.headers["location"]


async def _member(db_session: AsyncSession, user: dict, organization_id: str) -> None:
    db_session.add(
        OrganizationMember(
            organization_id=UUID(organization_id),
            user_id=UUID(user["id"]),
            role="ORG_MEMBER",
        )
    )
    await db_session.commit()


async def test_signing_in_with_slack_proves_the_slack_user_the_link_was_for(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    slack_sign_in,
):
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    me = _id("U")
    started = await _start(authenticated_client, workspace, me)
    assert started.status_code == 200, started.text
    url = started.json()["authorization_url"]
    assert url.startswith("https://slack.com/openid/connect/authorize?")
    state, slack_sign_in.nonce = _state_and_nonce(url)
    slack_sign_in.signed_in_as = (workspace.team, me)

    landed = await _callback(authenticated_client, state)

    assert "connected=1" in landed
    async with async_session_maker() as session:
        proven = await SlackManagerRepository(session).person(workspace.team, me)
    assert str(proven) == fixed_test_user["id"]
    # One Slack user per person per workspace: the installer row gave way.
    async with async_session_maker() as session:
        assert (
            await SlackManagerRepository(session).person(
                workspace.team, workspace.installer
            )
            is None
        )
    # Spent: the same state again proves nothing.
    again = await _callback(authenticated_client, state)
    assert "error=expired" in again


async def test_someone_elses_link_signed_in_as_yourself_is_refused(
    authenticated_client,
    async_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    slack_sign_in,
):
    """The phishing case: X sends V their own link. V signs in to Slack as V."""
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    victim = await _sign_up_verified_user(async_client)
    await _member(db_session, victim, test_pod["organization_id"])
    as_victim = {"Authorization": f"Bearer {victim['token']}"}
    attacker_slack, victim_slack = _id("U"), _id("U")

    started = await _start(authenticated_client, workspace, attacker_slack, **as_victim)
    assert started.status_code == 200, started.text
    state, slack_sign_in.nonce = _state_and_nonce(started.json()["authorization_url"])
    slack_sign_in.signed_in_as = (workspace.team, victim_slack)

    landed = await _callback(authenticated_client, state, **as_victim)

    assert "error=other_slack_user" in landed
    async with async_session_maker() as session:
        repo = SlackManagerRepository(session)
        assert await repo.person(workspace.team, attacker_slack) is None
        assert await repo.person(workspace.team, victim_slack) is None


async def test_a_sign_in_finished_by_another_lemma_session_is_refused(
    authenticated_client,
    async_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    slack_sign_in,
):
    """X starts it as themselves and sends V Slack's page: V's session comes back."""
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    victim = await _sign_up_verified_user(async_client)
    await _member(db_session, victim, test_pod["organization_id"])
    victim_slack = _id("U")
    started = await _start(authenticated_client, workspace, victim_slack)
    state, slack_sign_in.nonce = _state_and_nonce(started.json()["authorization_url"])
    slack_sign_in.signed_in_as = (workspace.team, victim_slack)

    landed = await _callback(
        authenticated_client,
        state,
        Authorization=f"Bearer {victim['token']}",
    )

    assert "error=session_changed" in landed
    async with async_session_maker() as session:
        assert (
            await SlackManagerRepository(session).person(workspace.team, victim_slack)
            is None
        )


async def test_a_sign_in_coming_back_with_no_lemma_session_is_refused_on_the_page(
    authenticated_client,
    test_app,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    slack_sign_in,
):
    """A browser that lost its Lemma session on Slack's side lands on the page
    with the reason, rather than on a bare 401 from the API."""
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    me = _id("U")
    started = await _start(authenticated_client, workspace, me)
    state, slack_sign_in.nonce = _state_and_nonce(started.json()["authorization_url"])
    slack_sign_in.signed_in_as = (workspace.team, me)

    # A browser with no session at all: no header, no cookie.
    async with AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://testserver"
    ) as signed_out:
        landed = await _callback(signed_out, state)

    assert "error=session_changed" in landed
    async with async_session_maker() as session:
        assert await SlackManagerRepository(session).person(workspace.team, me) is None


async def test_a_home_that_fails_to_refresh_still_lands_on_connected(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    slack_sign_in,
    monkeypatch,
):
    """The proof is committed before the Home refresh; Slack being unreachable
    for that refresh is no reason to tell the person it failed."""
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    me = _id("U")
    started = await _start(authenticated_client, workspace, me)
    state, slack_sign_in.nonce = _state_and_nonce(started.json()["authorization_url"])
    slack_sign_in.signed_in_as = (workspace.team, me)
    attempted = _home_cannot_publish(monkeypatch)

    landed = await _callback(authenticated_client, state)

    assert attempted == [me]
    assert "connected=1" in landed
    async with async_session_maker() as session:
        proven = await SlackManagerRepository(session).person(workspace.team, me)
    assert str(proven) == fixed_test_user["id"]


async def test_only_the_workspaces_organization_may_sign_in_there(
    authenticated_client,
    async_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    slack_sign_in,
):
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    outsider = await _sign_up_verified_user(async_client)
    refused = await _start(
        authenticated_client,
        workspace,
        _id("U"),
        Authorization=f"Bearer {outsider['token']}",
    )
    assert refused.status_code == 422, refused.text


async def test_the_installer_row_is_typed_install_not_a_profile_email(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    rows = (
        (
            await db_session.execute(
                text(
                    "SELECT proved_by FROM slack_workspace_people WHERE team_id = :team"
                ),
                {"team": workspace.team},
            )
        )
        .scalars()
        .all()
    )
    assert list(rows) == ["INSTALL"]
    install = await db_session.scalar(
        select(SlackManagerInstallModel).where(
            SlackManagerInstallModel.team_id == workspace.team
        )
    )
    assert install is not None and install.bot_user_id == workspace.manager_bot
