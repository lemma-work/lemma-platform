"""Lemma's own Slack app as the manager of a workspace's teammates, end to end.

It answers nobody. It binds a workspace to an organization, knows its people
by proof rather than by profile email, shows them their teammates, hands a
channel to the one they pick, and finishes adding a teammate once Slack's
Allow comes back.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.slack_manager_models import (
    SlackManagerInstallModel,
)
from app.modules.agent_surfaces.platforms.slack import manager_blocks as views
from app.modules.agent_surfaces.services.slack_manager import handle_event
from app.modules.agent_surfaces.services.slack_manager_context import person_link_url
from app.modules.agent_surfaces.tests.e2e.helpers import (
    E2E_SLACK_APP_ID,
    _create_agent_surface,
    _ensure_connector_account,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import wait_for_messages

pytestmark = pytest.mark.e2e

TEAM = "T-MANAGER-E2E"
MANAGER_BOT = "U-LEMMA-BOT"
INSTALLER = "U-INSTALLER"
STRANGER = "U-STRANGER"
TEAMMATE_BOT = "U-TEAMMATE-BOT"
MANAGER_TOKEN = "xoxb-manager-e2e"
TEAMMATE_TOKEN = "xoxb-teammate-e2e"


@pytest.fixture(autouse=True)
def lemma_app(monkeypatch):
    """The deployment's own Slack app is the one the e2e accounts were made by."""
    monkeypatch.setattr(
        "app.modules.agent_surfaces.config.surface_settings.slack_app_id",
        E2E_SLACK_APP_ID,
    )


def _uow_factory():
    return SessionUnitOfWorkFactory(async_session_maker)


async def _separate_slack_account(
    db_session: AsyncSession,
    *,
    user_id: str,
    config_source: str,
    install_name: str,
    credentials: dict,
):
    """A Slack account on an install of its own.

    ``_ensure_connector_account`` keeps one account per person and connector and
    overwrites it, which would make the manager and a teammate the same bot.
    They never are: Lemma's app and a teammate's app are two installs, and the
    person holds an account on each.
    """
    from app.modules.agent_surfaces.tests.e2e.helpers import _ensure_connector
    from app.modules.connectors.infrastructure.models.account import Account
    from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
    from app.modules.identity.infrastructure.models.organization_models import (
        OrganizationMember,
    )

    await _ensure_connector(db_session, "slack")
    organization_id = await db_session.scalar(
        select(OrganizationMember.organization_id)
        .where(OrganizationMember.user_id == UUID(user_id))
        .limit(1)
    )
    # Unique per call: install names are unique per organization, and a test
    # may hold several of Lemma's or a teammate's.
    install_name = f"{install_name}-{uuid4().hex[:6]}"
    install = AuthConfig(
        organization_id=organization_id,
        connector_id="slack",
        name=install_name,
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
        provider_account_id=f"e2e-slack-{install_name}",
        credentials=credentials,
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)
    return account


async def _manager_account(db_session, fixed_test_user, fake_slack):
    return await _separate_slack_account(
        db_session,
        user_id=fixed_test_user["id"],
        config_source="SYSTEM_DEFAULT",
        install_name="lemma-manager",
        credentials={
            "access_token": MANAGER_TOKEN,
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "app_id": E2E_SLACK_APP_ID,
                "bot_user_id": MANAGER_BOT,
                "team": {"id": TEAM, "name": "Acme"},
                "authed_user": {"id": INSTALLER},
                "api_base_url": fake_slack.base_url,
            },
        },
    )


async def _bind(client: AsyncClient, pod_id: str, account_id: UUID) -> dict:
    response = await client.put(
        f"/pods/{pod_id}/surface-setup/slack/manager",
        json={"account_id": str(account_id)},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _teammate_surface(client, db_session, fixed_test_user, fake_slack, pod_id):
    account = await _teammate_account(db_session, fixed_test_user, fake_slack)
    _, surface = await _create_agent_surface(
        client, pod_id, config={"type": "SLACK", "account_id": str(account.id)}
    )
    return surface


async def _teammate_account(db_session, fixed_test_user, fake_slack):
    return await _separate_slack_account(
        db_session,
        user_id=fixed_test_user["id"],
        config_source="ORG_CUSTOM",
        install_name="teammate-app",
        credentials={
            "access_token": TEAMMATE_TOKEN,
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "app_id": "A-TEAMMATE",
                "bot_user_id": TEAMMATE_BOT,
                "team": {"id": TEAM},
                "api_base_url": fake_slack.base_url,
            },
        },
    )


def _event(event: dict) -> SurfacePlatformWebhookIngress:
    return SurfacePlatformWebhookIngress(
        source="slack",
        payload={
            "type": "event_callback",
            "team_id": TEAM,
            "api_app_id": E2E_SLACK_APP_ID,
            "authorizations": [{"user_id": MANAGER_BOT, "is_bot": True}],
            "event": event,
        },
        # What verification resolves for the manager: its app, no teammate.
        receiver_surface_ids=[],
    )


def _view(message: dict) -> str:
    return str(message.get("view"))


async def test_binding_lemmas_app_makes_the_workspace_the_organizations(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    account = await _manager_account(db_session, fixed_test_user, fake_slack)
    bound = await _bind(authenticated_client, test_pod["id"], account.id)
    assert bound == {"team_id": TEAM, "team_name": "Acme"}

    listed = await authenticated_client.get(
        f"/pods/{test_pod['id']}/surface-setup/slack/manager"
    )
    assert listed.status_code == 200, listed.text
    assert [item["team_id"] for item in listed.json()["items"]] == [TEAM]


async def test_a_teammates_own_app_is_refused_as_the_manager(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        config_source="ORG_CUSTOM",
        credentials={
            "access_token": TEAMMATE_TOKEN,
            "raw_response": {
                "app_id": "A-OTHER",
                "bot_user_id": "U-X",
                "team": {"id": TEAM},
            },
        },
    )
    response = await authenticated_client.put(
        f"/pods/{test_pod['id']}/surface-setup/slack/manager",
        json={"account_id": str(account.id)},
    )
    assert response.status_code == 422, response.text
    assert "not Lemma's" in response.text


async def test_the_installer_sees_teammates_and_a_stranger_is_asked_to_connect(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    account = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(authenticated_client, test_pod["id"], account.id)

    # The installer was named by Slack's own OAuth grant, so they are known.
    assert await handle_event(
        _uow_factory(), _event({"type": "app_home_opened", "user": INSTALLER})
    )
    published = await wait_for_messages(
        message_store, "SLACK_VIEWS_PUBLISH", min_count=1
    )
    roster = _view(published[-1])
    assert "Teammates in Acme" in roster
    assert views.ADD_ACTION_ID in roster  # the pod has no Slack app yet
    assert MANAGER_TOKEN in published[-1].get("_authorization", "")

    # Someone else has proven nothing: one way forward, signing in once.
    await handle_event(
        _uow_factory(), _event({"type": "app_home_opened", "user": STRANGER})
    )
    published = await wait_for_messages(
        message_store, "SLACK_VIEWS_PUBLISH", min_count=2
    )
    assert "Connect your Lemma account" in _view(published[-1])
    assert "/slack/connect?" in _view(published[-1])


async def test_opening_the_home_link_signed_in_proves_who_the_slack_user_is(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    account = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(authenticated_client, test_pod["id"], account.id)
    query = parse_qs(urlsplit(person_link_url(TEAM, STRANGER)).query)

    linked = await authenticated_client.post(
        "/surface-setup/slack/people",
        json={
            "team_id": TEAM,
            "slack_user_id": STRANGER,
            "expires": int(query["exp"][0]),
            "signature": query["sig"][0],
        },
    )
    assert linked.status_code == 200, linked.text
    # Their Home turns from "connect" into their teammates on the spot.
    published = await wait_for_messages(
        message_store, "SLACK_VIEWS_PUBLISH", min_count=1
    )
    assert "Teammates in Acme" in _view(published[-1])

    forged = await authenticated_client.post(
        "/surface-setup/slack/people",
        json={
            "team_id": TEAM,
            "slack_user_id": "U-SOMEONE-ELSE",
            "expires": int(query["exp"][0]),
            "signature": query["sig"][0],
        },
    )
    assert forged.status_code == 422, forged.text


async def _invite_and_pick(fake_slack, message_store, surface, channel: str) -> None:
    await handle_event(
        _uow_factory(),
        _event(
            {
                "type": "member_joined_channel",
                "user": MANAGER_BOT,
                "inviter": INSTALLER,
                "channel": channel,
            }
        ),
    )
    picker = await wait_for_messages(message_store, "SLACK_EPHEMERAL", min_count=1)
    assert picker[-1]["user"] == INSTALLER
    assert "Who should join" in str(picker[-1].get("text"))
    assert surface["id"] in str(picker[-1].get("blocks"))

    await handle_event(
        _uow_factory(),
        SurfacePlatformWebhookIngress(
            source="slack",
            payload={
                "type": "block_actions",
                "api_app_id": E2E_SLACK_APP_ID,
                "team": {"id": TEAM},
                "user": {"id": INSTALLER},
                "actions": [
                    {
                        "action_id": f"{views.JOIN_ACTION_ID}:0",
                        "value": json.dumps(
                            {"channel": channel, "surface": surface["id"]}
                        ),
                    }
                ],
            },
            receiver_surface_ids=[],
        ),
    )


async def test_invited_to_a_public_channel_it_brings_the_teammate_and_leaves(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    account = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(authenticated_client, test_pod["id"], account.id)
    surface = await _teammate_surface(
        authenticated_client, db_session, fixed_test_user, fake_slack, test_pod["id"]
    )

    await _invite_and_pick(fake_slack, message_store, surface, "C-PUBLIC")

    joins = await wait_for_messages(message_store, "SLACK_JOIN", min_count=1)
    # The teammate joins by itself, with its own token.
    assert joins[-1]["channel"] == "C-PUBLIC"
    assert TEAMMATE_TOKEN in joins[-1].get("_authorization", "")
    leaves = await wait_for_messages(message_store, "SLACK_LEAVE", min_count=1)
    assert MANAGER_TOKEN in leaves[-1].get("_authorization", "")
    hello = await wait_for_messages(message_store, "SLACK", min_count=1)
    assert hello[-1]["channel"] == "C-PUBLIC" and "Mention me" in hello[-1]["text"]

    # And it answers there now.
    updated = await authenticated_client.get(
        f"/pods/{test_pod['id']}/surfaces/{surface['name']}"
    )
    channels = [route["channel_id"] for route in updated.json()["config"]["channels"]]
    assert "C-PUBLIC" in channels


async def test_invited_to_a_private_channel_it_invites_the_teammate_in(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    account = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(authenticated_client, test_pod["id"], account.id)
    surface = await _teammate_surface(
        authenticated_client, db_session, fixed_test_user, fake_slack, test_pod["id"]
    )
    fake_slack.private_channels.add("G-PRIVATE")

    await _invite_and_pick(fake_slack, message_store, surface, "G-PRIVATE")

    invites = await wait_for_messages(message_store, "SLACK_INVITE", min_count=1)
    # A bot cannot join a private channel; the manager, already in, invites it.
    assert invites[-1]["users"] == TEAMMATE_BOT
    assert MANAGER_TOKEN in invites[-1].get("_authorization", "")
    assert not message_store.get_all("SLACK_JOIN")


async def test_a_message_to_the_manager_is_pointed_at_the_teammates(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    account = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(authenticated_client, test_pod["id"], account.id)
    await _teammate_surface(
        authenticated_client, db_session, fixed_test_user, fake_slack, test_pod["id"]
    )

    await handle_event(
        _uow_factory(),
        _event(
            {
                "type": "message",
                "channel_type": "im",
                "user": INSTALLER,
                "channel": "D1",
            }
        ),
    )
    replies = await wait_for_messages(message_store, "SLACK", min_count=1)
    assert f"<@{TEAMMATE_BOT}>" in replies[-1]["text"]
    assert "don't answer questions" in replies[-1]["text"]


async def test_an_allowed_app_becomes_the_teammates_surface_and_says_so(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (
        SlackManagerRepository,
    )
    from app.modules.agent_surfaces.services.slack_manager_bots import bind_allowed_app

    manager = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(authenticated_client, test_pod["id"], manager.id)
    allowed = await _teammate_account(db_session, fixed_test_user, fake_slack)
    user_id = UUID(fixed_test_user["id"])
    async with _uow_factory()() as uow:
        await SlackManagerRepository(uow.session).add_pending_binding(
            auth_config_id=allowed.auth_config_id,
            pod_id=UUID(test_pod["id"]),
            team_id=TEAM,
            app_id="A-TEAMMATE",
            requested_by_user_id=user_id,
            slack_user_id=INSTALLER,
        )
        await uow.commit()

    async with _uow_factory()() as uow:
        assert await bind_allowed_app(
            uow,
            auth_config_id=allowed.auth_config_id,
            account_id=allowed.id,
            user_id=user_id,
        )

    surfaces = await authenticated_client.get(f"/pods/{test_pod['id']}/surfaces")
    assert any(item["platform"] == "SLACK" for item in surfaces.json()["items"])
    told = await wait_for_messages(message_store, "SLACK", min_count=1)
    assert f"<@{TEAMMATE_BOT}> is in Slack now" in told[-1]["text"]

    # Spent: the same Allow arriving again makes nothing.
    async with _uow_factory()() as uow:
        assert not await bind_allowed_app(
            uow,
            auth_config_id=allowed.auth_config_id,
            account_id=allowed.id,
            user_id=user_id,
        )


async def test_removing_lemma_from_the_workspace_ends_the_binding(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    account = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(authenticated_client, test_pod["id"], account.id)

    await handle_event(_uow_factory(), _event({"type": "app_uninstalled"}))

    remaining = await db_session.scalar(
        select(SlackManagerInstallModel).where(SlackManagerInstallModel.team_id == TEAM)
    )
    assert remaining is None


SIGNING_SECRET = "lemma-app-signing-secret"


async def _signed_interaction(client: AsyncClient, payload: dict):
    from urllib.parse import urlencode

    from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
        build_slack_signature_headers,
    )

    raw_body = urlencode({"payload": json.dumps(payload)}).encode()
    headers = {
        key: value
        for key, value in build_slack_signature_headers(
            raw_body=raw_body, signing_secret=SIGNING_SECRET
        ).items()
        if key.lower() != "content-type"
    }
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    return await client.post(
        "/surfaces/webhooks/slack", content=raw_body, headers=headers
    )


async def test_add_to_slack_opens_its_modal_inside_the_webhook_request(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    """Signed by Lemma's own app in a workspace with no teammate on it: it
    verifies through the manager's candidate, and the modal opens before the
    request returns -- Slack's trigger is gone in 3 seconds."""
    monkeypatch.setattr(
        "app.modules.agent_surfaces.config.surface_settings.surface_webhook_security_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.config.surface_settings.slack_signing_secret",
        SIGNING_SECRET,
    )
    account = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(authenticated_client, test_pod["id"], account.id)

    response = await _signed_interaction(
        authenticated_client,
        {
            "type": "block_actions",
            "api_app_id": E2E_SLACK_APP_ID,
            "team": {"id": TEAM},
            "user": {"id": INSTALLER},
            "trigger_id": "trigger-add",
            "actions": [{"action_id": views.ADD_ACTION_ID, "value": test_pod["id"]}],
        },
    )
    assert response.status_code == 200, response.text

    opened = await wait_for_messages(message_store, "SLACK_VIEWS_OPEN", min_count=1)
    modal = _view(opened[-1])
    # No configuration token saved yet: both ways are offered.
    assert views.TOKEN_BLOCK_ID in modal and views.CLIENT_ID_BLOCK_ID in modal
    assert "/surface-setup/slack/manifest/new-app?agent_name=" in modal
    assert MANAGER_TOKEN in opened[-1].get("_authorization", "")


async def test_half_an_own_app_is_sent_back_to_the_modal(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.modules.agent_surfaces.config.surface_settings.surface_webhook_security_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.config.surface_settings.slack_signing_secret",
        SIGNING_SECRET,
    )
    account = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(authenticated_client, test_pod["id"], account.id)

    response = await _signed_interaction(
        authenticated_client,
        {
            "type": "view_submission",
            "api_app_id": E2E_SLACK_APP_ID,
            "team": {"id": TEAM},
            "user": {"id": INSTALLER},
            "view": {
                "id": "V-ADD",
                "callback_id": views.ADD_VIEW_CALLBACK_ID,
                "private_metadata": json.dumps({"pod": test_pod["id"]}),
                "state": {
                    "values": {
                        views.CLIENT_ID_BLOCK_ID: {
                            views.INPUT_ACTION_ID: {"value": "cid"}
                        }
                    }
                },
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["response_action"] == "errors"
    assert set(body["errors"]) == {
        views.CLIENT_SECRET_BLOCK_ID,
        views.SIGNING_SECRET_BLOCK_ID,
    }


async def _slack_accepts_an_orgs_own_app(db_session: AsyncSession):
    """What the real catalog says of Slack, and the bare e2e connector does not:
    an org may bring its own app, and here is where Slack's OAuth lives."""
    from app.modules.connectors.infrastructure.models.connector import Connector

    connector = await db_session.get(Connector, "slack")
    original = list(connector.kinds or [])
    connector.kinds = [
        {
            "kind": "http",
            "auth_scheme": "OAUTH2",
            "supports_org_custom_oauth": True,
            "oauth2_defaults": {
                "authorization_url": "https://slack.com/oauth/v2/authorize",
                "token_url": "https://slack.com/api/oauth.v2.access",
                "default_scopes": ["chat:write"],
            },
            "system_oauth": {
                "client_id_env": "SLACK_CLIENT_ID",
                "client_secret_env": "SLACK_CLIENT_SECRET",
            },
        }
    ]
    await db_session.commit()

    async def restore() -> None:
        row = await db_session.get(Connector, "slack")
        row.kinds = original
        await db_session.commit()

    return restore


async def test_an_app_made_by_hand_in_slack_is_registered_and_offered_for_allow(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    """The by-hand path never calls Slack's app API: three pasted values become
    the org's own install, and the modal turns into its Allow."""
    from app.modules.agent_surfaces.infrastructure.slack_manager_models import (
        SlackPendingAppBindingModel,
    )

    account = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(authenticated_client, test_pod["id"], account.id)
    restore = await _slack_accepts_an_orgs_own_app(db_session)

    submission = {
        "type": "view_submission",
        "api_app_id": E2E_SLACK_APP_ID,
        "team": {"id": TEAM},
        "user": {"id": INSTALLER},
        "view": {
            "id": "V-ADD",
            "callback_id": views.ADD_VIEW_CALLBACK_ID,
            "private_metadata": json.dumps({"pod": test_pod["id"]}),
            "state": {
                "values": {
                    block: {views.INPUT_ACTION_ID: {"value": value}}
                    for block, value in (
                        (views.CLIENT_ID_BLOCK_ID, "1234.5678"),
                        (views.CLIENT_SECRET_BLOCK_ID, "client-secret"),
                        (views.SIGNING_SECRET_BLOCK_ID, "signing-secret"),
                    )
                }
            },
        },
    }
    assert await handle_event(
        _uow_factory(),
        SurfacePlatformWebhookIngress(
            source="slack", payload=submission, receiver_surface_ids=[]
        ),
    )

    updated = await wait_for_messages(message_store, "SLACK_VIEWS_UPDATE", min_count=1)
    assert updated[-1]["view_id"] == "V-ADD"
    assert "Allow" in _view(updated[-1]) and "is ready" in _view(updated[-1])
    pending = await db_session.scalar(
        select(SlackPendingAppBindingModel).where(
            SlackPendingAppBindingModel.team_id == TEAM
        )
    )
    await restore()
    assert pending is not None and str(pending.pod_id) == test_pod["id"]


async def test_a_teammates_own_bot_invited_by_its_editor_takes_the_channel(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    """The invite was the yes: no "who should answer here?" for a bot that is
    one pod's, only the channel taken and one line said."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent_surfaces.composition import build_app_event_handler
    from app.modules.agent_surfaces.tests.e2e.helpers import _seed_external_user

    surface = await _teammate_surface(
        authenticated_client, db_session, fixed_test_user, fake_slack, test_pod["id"]
    )
    await _seed_external_user(
        db_session,
        platform="SLACK",
        external_user_id=INSTALLER,
        resolved_user_id=UUID(fixed_test_user["id"]),
        tenant_id=TEAM,
    )
    handler = build_app_event_handler(SqlAlchemyUnitOfWork(db_session))
    handled = await handler.try_handle_lifecycle(
        SurfacePlatformWebhookIngress(
            source="slack",
            payload={
                "type": "event_callback",
                "team_id": TEAM,
                "api_app_id": "A-TEAMMATE",
                "authorizations": [{"user_id": TEAMMATE_BOT, "is_bot": True}],
                "event": {
                    "type": "member_joined_channel",
                    "user": TEAMMATE_BOT,
                    "inviter": INSTALLER,
                    "channel": "C-DIRECT",
                },
            },
            receiver_surface_ids=[UUID(surface["id"])],
        )
    )
    assert handled is True

    hello = await wait_for_messages(message_store, "SLACK", min_count=1)
    assert hello[-1]["channel"] == "C-DIRECT" and "Mention me" in hello[-1]["text"]
    assert TEAMMATE_TOKEN in hello[-1].get("_authorization", "")
    assert not message_store.get_all("SLACK_EPHEMERAL")
    updated = await authenticated_client.get(
        f"/pods/{test_pod['id']}/surfaces/{surface['name']}"
    )
    assert "C-DIRECT" in [r["channel_id"] for r in updated.json()["config"]["channels"]]


async def test_a_bot_token_revoked_for_another_app_leaves_lemma_installed(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    account = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(authenticated_client, test_pod["id"], account.id)

    await handle_event(
        _uow_factory(),
        _event({"type": "tokens_revoked", "tokens": {"bot": ["U-SOME-OTHER-BOT"]}}),
    )

    assert await db_session.scalar(
        select(SlackManagerInstallModel).where(SlackManagerInstallModel.team_id == TEAM)
    )


async def test_a_link_is_named_before_it_connects_anything(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    """The page asks "is this you?" first: a link somebody else sent must not
    connect their Slack account to whoever opens it."""
    from app.modules.agent_surfaces.infrastructure.slack_manager_models import (
        SlackWorkspacePersonModel,
    )

    account = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(authenticated_client, test_pod["id"], account.id)
    fake_slack.user_overrides[STRANGER] = {
        "email": "x@elsewhere.test",
        "display_name": "Mallory",
    }
    query = parse_qs(urlsplit(person_link_url(TEAM, STRANGER)).query)
    body = {
        "team_id": TEAM,
        "slack_user_id": STRANGER,
        "expires": int(query["exp"][0]),
        "signature": query["sig"][0],
    }

    preview = await authenticated_client.post(
        "/surface-setup/slack/people/preview", json=body
    )

    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "team_id": TEAM,
        "team_name": "Acme",
        "slack_user_name": "Mallory",
    }
    assert not await db_session.scalar(
        select(SlackWorkspacePersonModel).where(
            SlackWorkspacePersonModel.slack_user_id == STRANGER
        )
    )


async def test_an_allow_by_someone_else_is_explained_to_whoever_asked(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (
        SlackManagerRepository,
    )
    from app.modules.agent_surfaces.services.slack_manager_bots import bind_allowed_app

    manager = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(authenticated_client, test_pod["id"], manager.id)
    allowed = await _teammate_account(db_session, fixed_test_user, fake_slack)
    async with _uow_factory()() as uow:
        await SlackManagerRepository(uow.session).add_pending_binding(
            auth_config_id=allowed.auth_config_id,
            pod_id=UUID(test_pod["id"]),
            team_id=TEAM,
            app_id="A-TEAMMATE",
            requested_by_user_id=UUID(fixed_test_user["id"]),
            slack_user_id=INSTALLER,
        )
        await uow.commit()

    async with _uow_factory()() as uow:
        assert not await bind_allowed_app(
            uow,
            auth_config_id=allowed.auth_config_id,
            account_id=allowed.id,
            user_id=uuid4(),
        )

    told = await wait_for_messages(message_store, "SLACK", min_count=1)
    assert "Someone else allowed" in told[-1]["text"]
