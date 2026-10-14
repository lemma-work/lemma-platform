"""Adding Lemma to Slack from "Add to Slack", end to end.

Sign in, Slack's consent, and the workspace adopts itself: Lemma's app becomes
its manager and says hello with the teammate to add first.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.infrastructure.slack_manager_models import (
    SlackManagerInstallModel,
)
from app.modules.agent_surfaces.platforms.slack import manager_blocks as views
from app.modules.agent_surfaces.services.slack_install import adopt_workspace
from app.modules.agent_surfaces.tests.e2e.helpers import E2E_SLACK_APP_ID
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import wait_for_messages
from app.modules.agent_surfaces.tests.e2e.test_slack_manager_e2e import (
    TEAM,
    _bind,
    _manager_account,
    _slack_accepts_an_orgs_own_app,
    _uow_factory,
)

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def lemma_app(monkeypatch):
    monkeypatch.setattr(
        "app.modules.agent_surfaces.config.surface_settings.slack_app_id",
        E2E_SLACK_APP_ID,
    )


async def _adopt(fixed_test_user, account) -> bool:
    async with _uow_factory()() as uow:
        return await adopt_workspace(
            uow,
            user_id=UUID(fixed_test_user["id"]),
            organization_id=account.organization_id,
            account_id=account.id,
        )


async def _organizations_of(db_session: AsyncSession, fixed_test_user) -> set:
    rows = await db_session.execute(
        text("SELECT organization_id FROM organization_members WHERE user_id = :user"),
        {"user": UUID(fixed_test_user["id"])},
    )
    return {row[0] for row in rows}


async def test_lemmas_grant_for_an_unheld_workspace_adopts_it_and_says_hello(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    account = await _manager_account(db_session, fixed_test_user, fake_slack)

    assert await _adopt(fixed_test_user, account)

    install = await db_session.scalar(
        select(SlackManagerInstallModel).where(SlackManagerInstallModel.team_id == TEAM)
    )
    assert install is not None and install.organization_id == account.organization_id
    welcome = await wait_for_messages(message_store, "SLACK", min_count=1)
    assert "Lemma is in Acme" in welcome[-1]["text"]
    # The one thing worth doing next, as a button: add the first teammate.
    assert views.ADD_ACTION_ID in str(welcome[-1].get("blocks"))


async def test_a_second_grant_for_the_same_workspace_says_nothing_more(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    account = await _manager_account(db_session, fixed_test_user, fake_slack)
    assert await _adopt(fixed_test_user, account)
    before = len(message_store.get_all("SLACK"))

    assert not await _adopt(fixed_test_user, account)
    assert len(message_store.get_all("SLACK")) == before


async def test_a_workspace_another_organization_holds_is_not_adopted(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    from app.modules.identity.infrastructure.models.organization_models import (
        Organization,
    )

    other = Organization(
        name="Other", slug=f"other-{uuid4().hex[:8]}", join_policy="INVITE_ONLY"
    )
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        SlackManagerInstallModel(
            team_id=TEAM,
            organization_id=other.id,
            account_id=(
                await _manager_account(db_session, fixed_test_user, fake_slack)
            ).id,
            bot_user_id="U-ELSEWHERE",
        )
    )
    await db_session.commit()
    account = await _manager_account(db_session, fixed_test_user, fake_slack)

    assert not await _adopt(fixed_test_user, account)
    install = await db_session.scalar(
        select(SlackManagerInstallModel).where(SlackManagerInstallModel.team_id == TEAM)
    )
    assert install is not None and install.organization_id == other.id
    # Not silent: the app that was installed says why it is not staying.
    told = await wait_for_messages(message_store, "SLACK", min_count=1)
    assert "Lemma couldn't join this workspace" in told[-1]["text"]
    assert "another Lemma organization" in told[-1]["text"]


async def test_binding_from_the_web_says_hello_too(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    account = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(authenticated_client, test_pod["id"], account.id)

    welcome = await wait_for_messages(message_store, "SLACK", min_count=1)
    assert "Lemma is in Acme" in welcome[-1]["text"]


async def test_add_to_slack_answers_with_slacks_consent_for_lemmas_app(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    monkeypatch,
):
    """Signed in, with an organization already: straight to Slack's consent,
    against the organization's install of Lemma's own app."""
    monkeypatch.setenv("SLACK_CLIENT_ID", "1234.lemma")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "lemma-secret")
    await _manager_account(db_session, fixed_test_user, fake_slack)  # the connector row
    memberships = await _organizations_of(db_session, fixed_test_user)
    restore = await _slack_accepts_an_orgs_own_app(db_session)
    try:
        response = await authenticated_client.post("/surface-setup/slack/install")
    finally:
        await restore()

    assert response.status_code == 200, response.text
    # The organization they are in, the one checked: nothing provisioned beside it.
    assert await _organizations_of(db_session, fixed_test_user) == memberships
    url = response.json()["authorization_url"]
    assert url.startswith("https://slack.com/oauth/v2/authorize")
    assert parse_qs(urlsplit(url).query)["client_id"] == ["1234.lemma"]
