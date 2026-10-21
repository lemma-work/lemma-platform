"""Slack removing an app, from the signed webhook to what it leaves behind.

Slack sends ``app_uninstalled`` (or ``tokens_revoked`` naming the bot) and then
nothing: no leave per channel, and every later send refused. Through the real
route and the real platform-update job, the app's connector account is marked
``REAUTH_REQUIRED`` -- which the surface's setup reads as "install it again" --
and every group its bot was in is archived, as the bot taken out of one is.
Invited back after a reinstall, a group returns. Only the app the event is
about is touched: another app in the same workspace keeps answering, even when
the request's signature is not checked and every app's surfaces receive it.

Each test has a workspace and bots of its own: an uninstall reaches every
surface of the app in the workspace, which in a shared database would be other
tests' too.
"""

from __future__ import annotations

import json
import secrets
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.composition import build_surface_ingress
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent,
    _ensure_connector_account,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_slack_signature_headers,
)
from app.modules.agent_surfaces.tests.e2e.platform_payloads import (
    slack as slack_payloads,
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
from app.modules.connectors.infrastructure.models.account import Account

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.drives_work,
    pytest.mark.usefixtures("clean_surface_work"),
]

SIGNING_SECRET = "slack-removal-secret"
OUR_APP = "A0REMOVED1"
THEIR_APP = "A0STAYING1"


def _id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:9].upper()}"


def _ts() -> str:
    return f"17{secrets.randbelow(10**8):08d}.{secrets.randbelow(10**6):06d}"


class _Workspace:
    """One Slack workspace no other test uses, with a channel in it."""

    def __init__(self) -> None:
        self.team = _id("T")
        self.channel = _id("C")


async def _slack_bot(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    pod_id: str,
    user_id: str,
    workspace: _Workspace,
    app_id: str,
    fake_slack: Any,
) -> tuple[dict, UUID, str]:
    """A Slack surface on an account of its own: one app's bot in the
    workspace, answering in its channel. Returns the surface, its account's id
    its bot's user id."""
    base = await _ensure_connector_account(
        db_session, user_id=user_id, connector_id="slack", credentials={}
    )
    bot = _id("U")
    account = Account(
        user_id=base.user_id,
        organization_id=base.organization_id,
        auth_config_id=base.auth_config_id,
        connector_id="slack",
        provider_account_id=f"e2e-slack-{bot}",
        credentials={
            "access_token": f"xoxb-{bot.lower()}",
            "scope": "chat:write",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "app_id": app_id,
                "bot_user_id": bot,
                "team_id": workspace.team,
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)
    agent = await _create_agent(client, pod_id)
    response = await client.post(
        f"/pods/{pod_id}/surfaces",
        json={
            "platform": "SLACK",
            "account_id": str(account.id),
            "name": f"slack-{bot.lower()}",
            "default_agent_name": agent["name"],
            "config": {"channels": [{"channel_id": workspace.channel}]},
        },
    )
    assert response.status_code == 200, response.text
    return response.json(), account.id, bot


async def _heard_in_channel(
    db_session: AsyncSession, workspace: _Workspace, *, bot: str, app_id: str
) -> None:
    """A mention of the bot in the channel: what makes its group, and the
    group's channel in Lemma."""
    payload = slack_payloads.envelope(
        text=f"<@{bot}> is the deploy on?",
        ts=_ts(),
        channel=workspace.channel,
        channel_type="channel",
        event_type="app_mention",
    )
    payload["team_id"] = workspace.team
    payload["api_app_id"] = app_id
    payload["event"]["team"] = workspace.team
    payload["authorizations"][0].update(team_id=workspace.team, user_id=bot)
    uow = SqlAlchemyUnitOfWork(db_session)
    await build_surface_ingress(uow).prepare_ingress(
        SurfacePlatformWebhookIngress(source="slack", payload=payload, headers={})
    )
    await uow.commit()


async def _slack_says(
    client: AsyncClient,
    workspace: _Workspace,
    event: dict[str, Any],
    *,
    app_id: str = OUR_APP,
    bot: str | None = None,
    signed: bool = True,
) -> None:
    """One event callback, POSTed to the shared route the way Slack sends it.
    ``bot`` is the app's bot the delivery is authorized for, which Slack names
    on an event about a channel (``authorizations``)."""
    payload: dict[str, Any] = {
        "type": "event_callback",
        "team_id": workspace.team,
        "api_app_id": app_id,
        "event": event,
        "event_id": _id("Ev"),
        "event_time": 1700000000,
    }
    if bot is not None:
        payload["authorizations"] = [
            {"team_id": workspace.team, "user_id": bot, "is_bot": True}
        ]
    raw = json.dumps(payload).encode()
    headers = (
        build_slack_signature_headers(raw_body=raw, signing_secret=SIGNING_SECRET)
        if signed
        else {}
    )
    headers["content-type"] = "application/json"
    response = await client.post(
        "/surfaces/webhooks/slack", content=raw, headers=headers
    )
    assert response.status_code == 200, response.text


async def _account_status(db_session: AsyncSession, account_id: UUID) -> str:
    db_session.expire_all()
    return str(
        await db_session.scalar(select(Account.status).where(Account.id == account_id))
    )


async def _archived(db_session: AsyncSession, workspace: _Workspace) -> list[bool]:
    """Whether each mirror of the workspace's channel is archived."""
    db_session.expire_all()
    rows = await db_session.scalars(
        select(ChannelModel.archived_at)
        .join(ChannelSourceModel, ChannelSourceModel.channel_id == ChannelModel.id)
        .where(ChannelSourceModel.external_channel_id == workspace.channel)
    )
    return [archived_at is not None for archived_at in rows]


@pytest.fixture
def signed_by_lemmas_app(monkeypatch):
    monkeypatch.setattr(
        surface_settings, "slack_signing_secret", "slack-removal-secret"
    )


@pytest.mark.usefixtures("signed_by_lemmas_app")
async def test_the_app_removed_retires_its_account_and_archives_its_groups(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    pod_id = test_pod["id"]
    workspace = _Workspace()
    surface, account, bot = await _slack_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
        workspace=workspace,
        app_id=OUR_APP,
        fake_slack=fake_slack,
    )
    await _heard_in_channel(db_session, workspace, bot=bot, app_id=OUR_APP)
    assert await _archived(db_session, workspace) == [False]
    setup_path = f"/pods/{pod_id}/surfaces/{surface['name']}/setup"
    before = (await authenticated_client.get(setup_path)).json()
    assert before["actions"] == [] and before["ready"] is True

    await _slack_says(authenticated_client, workspace, {"type": "app_uninstalled"})
    # Classified in the request as an update about the app, not dropped.
    assert (
        await scalar(
            "SELECT count(*) FROM work_items WHERE kind = 'surface.platform_update'"
        )
        == 1
    )
    await drain(surface_registry())

    assert await _account_status(db_session, account) == "REAUTH_REQUIRED"
    assert await _archived(db_session, workspace) == [True]
    after = await authenticated_client.get(setup_path)
    assert after.status_code == 200, after.text
    setup = after.json()
    assert [action["key"] for action in setup["actions"]] == ["slack_reinstall"]
    assert setup["actions"][0]["link_label"] == "Reconnect Slack"
    assert (setup["status"], setup["ready"]) == ("NEEDS_SETUP", False)

    # Reinstalled -- the account repaired in place -- and invited back.
    await db_session.execute(
        update(Account).where(Account.id == account).values(status="CONNECTED")
    )
    await db_session.commit()
    await _slack_says(
        authenticated_client,
        workspace,
        {
            "type": "member_joined_channel",
            "user": bot,
            "channel": workspace.channel,
            "channel_type": "C",
            "team": workspace.team,
        },
        bot=bot,
    )
    await drain(surface_registry())
    assert await _archived(db_session, workspace) == [False]
    assert (await authenticated_client.get(setup_path)).json()["actions"] == []


@pytest.mark.usefixtures("signed_by_lemmas_app")
async def test_a_surface_waiting_on_setup_hears_its_app_removed_too(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    """Not answering is not gone: brought back, it would read as connected to
    an app that is not there."""
    workspace = _Workspace()
    surface, account, bot = await _slack_bot(
        authenticated_client,
        db_session,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
        workspace=workspace,
        app_id=OUR_APP,
        fake_slack=fake_slack,
    )
    await db_session.execute(
        update(AgentSurface)
        .where(AgentSurface.id == UUID(surface["id"]))
        .values(status="NEEDS_SETUP")
    )
    await db_session.commit()

    await _slack_says(
        authenticated_client,
        workspace,
        {"type": "tokens_revoked", "tokens": {"bot": [bot]}},
    )
    await drain(surface_registry())

    assert await _account_status(db_session, account) == "REAUTH_REQUIRED"


@pytest.mark.usefixtures("signed_by_lemmas_app")
async def test_another_app_in_the_workspace_keeps_answering_unverified(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    monkeypatch,
):
    """With signatures unchecked (local development) a delivery reaches every
    app's surfaces in the workspace. The removal names one app, and only that
    one is stood down."""
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", False)
    pod_id = test_pod["id"]
    workspace = _Workspace()
    _, removed, removed_bot = await _slack_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
        workspace=workspace,
        app_id=OUR_APP,
        fake_slack=fake_slack,
    )
    _, staying, staying_bot = await _slack_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
        workspace=workspace,
        app_id=THEIR_APP,
        fake_slack=fake_slack,
    )
    await _heard_in_channel(db_session, workspace, bot=removed_bot, app_id=OUR_APP)
    await _heard_in_channel(db_session, workspace, bot=staying_bot, app_id=THEIR_APP)

    await _slack_says(
        authenticated_client, workspace, {"type": "app_uninstalled"}, signed=False
    )
    await drain(surface_registry())

    assert await _account_status(db_session, removed) == "REAUTH_REQUIRED"
    assert await _account_status(db_session, staying) == "CONNECTED"
    assert sorted(await _archived(db_session, workspace)) == [False, True]
