"""The people of a Slack workspace Lemma manages, as a teammate there sees them.

Members route as themselves -- by proof, when they connected through Lemma's
app, not by a profile email. Anyone else in the workspace is answered: a
contact in a private chat, an outsider in a channel. Never a seat, and never
asked to sign up first. Slack's guests are outsiders throughout.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.composition import build_surface_ingress
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    E2E_SLACK_APP_ID,
    _load_slack_dm_fixture,
)
from app.modules.agent_surfaces.tests.e2e.test_slack_manager_e2e import (
    TEAM,
    TEAMMATE_BOT,
    _bind,
    _invite_and_pick,
    _manager_account,
    _teammate_surface,
    _uow_factory,
)

pytestmark = pytest.mark.e2e

STRANGER = "U-COLLEAGUE"
GUEST = "U-GUEST"


@pytest.fixture(autouse=True)
def lemma_app(monkeypatch):
    monkeypatch.setattr(
        "app.modules.agent_surfaces.config.surface_settings.slack_app_id",
        E2E_SLACK_APP_ID,
    )


async def _managed_teammate(client, db_session, fixed_test_user, fake_slack, pod_id):
    """Lemma's app manages the workspace, then the teammate's own bot arrives."""
    manager = await _manager_account(db_session, fixed_test_user, fake_slack)
    await _bind(client, pod_id, manager.id)
    return await _teammate_surface(
        client, db_session, fixed_test_user, fake_slack, pod_id
    )


def _dm_from(sender: str, surface: dict, *, text: str = "where is the launch doc?"):
    payload = _load_slack_dm_fixture(text=text)
    payload["team_id"] = TEAM
    payload["api_app_id"] = "A-TEAMMATE"
    payload["authorizations"] = [
        {"team_id": TEAM, "user_id": TEAMMATE_BOT, "is_bot": True}
    ]
    payload["event"]["user"] = sender
    payload["event"]["team"] = TEAM
    payload["event"].pop("assistant_thread", None)
    return SurfacePlatformWebhookIngress(
        source="slack",
        payload=payload,
        headers={},
        receiver_surface_ids=[UUID(surface["id"])],
    )


async def _prepare(db_session, request):
    uow = SqlAlchemyUnitOfWork(db_session)
    context = await build_surface_ingress(uow).prepare_ingress(request)
    await uow.commit()
    return context


async def _slack_contact(db_session, pod_id: str, sender: str):
    from app.modules.contacts.infrastructure.models import ContactIdentityModel

    return await db_session.scalar(
        select(ContactIdentityModel).where(
            ContactIdentityModel.pod_id == UUID(pod_id),
            ContactIdentityModel.kind == "SLACK",
            ContactIdentityModel.value == f"{TEAM}:{sender}",
        )
    )


async def test_a_teammate_in_a_managed_workspace_answers_its_people_by_default(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    surface = await _managed_teammate(
        authenticated_client, db_session, fixed_test_user, fake_slack, test_pod["id"]
    )
    detail = await authenticated_client.get(
        f"/pods/{test_pod['id']}/surfaces/{surface['name']}"
    )
    contacts = detail.json()["config"]["contacts"]
    assert contacts["answer"] == "anyone"
    assert contacts["looked_after_by"] == fixed_test_user["id"]


async def test_a_colleague_outside_the_pod_is_answered_as_a_contact_not_signed_up(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    surface = await _managed_teammate(
        authenticated_client, db_session, fixed_test_user, fake_slack, test_pod["id"]
    )
    fake_slack.user_overrides[STRANGER] = {"email": "colleague@elsewhere.test"}

    context = await _prepare(db_session, _dm_from(STRANGER, surface))

    assert isinstance(context, SurfaceChatContext), context
    assert context.audience.answers_outsiders
    contact = await _slack_contact(db_session, test_pod["id"], STRANGER)
    assert contact is not None and contact.strength == "CHANNEL"


async def test_a_slack_guest_is_not_one_of_the_workspaces_people(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    surface = await _managed_teammate(
        authenticated_client, db_session, fixed_test_user, fake_slack, test_pod["id"]
    )
    fake_slack.user_overrides[GUEST] = {
        "email": "guest@agency.test",
        "is_restricted": True,
    }

    context = await _prepare(db_session, _dm_from(GUEST, surface))

    assert not isinstance(context, SurfaceChatContext)
    assert await _slack_contact(db_session, test_pod["id"], GUEST) is None


async def test_a_person_proven_through_lemmas_app_routes_as_themselves(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    """Their Slack email is not their Lemma one; the proof is what counts."""
    from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (
        SlackManagerRepository,
    )

    surface = await _managed_teammate(
        authenticated_client, db_session, fixed_test_user, fake_slack, test_pod["id"]
    )
    fake_slack.user_overrides[STRANGER] = {"email": "personal@elsewhere.test"}
    async with _uow_factory()() as uow:
        await SlackManagerRepository(uow.session).link_person(
            team_id=TEAM, slack_user_id=STRANGER, user_id=UUID(fixed_test_user["id"])
        )
        await uow.commit()

    context = await _prepare(db_session, _dm_from(STRANGER, surface))

    assert isinstance(context, SurfaceChatContext)
    assert str(context.user_id) == fixed_test_user["id"]
    assert not context.audience.answers_outsiders
    assert await _slack_contact(db_session, test_pod["id"], STRANGER) is None


async def test_nobody_in_a_managed_workspace_is_asked_to_sign_up_first(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    from app.modules.agent_surfaces.infrastructure.adapters.registry import (
        SurfacePlatformAdapterRegistry,
    )
    from app.modules.agent_surfaces.services.onboarding_transport import (
        resolve_onboarding_transport,
    )

    surface = await _managed_teammate(
        authenticated_client, db_session, fixed_test_user, fake_slack, test_pod["id"]
    )
    transport = await resolve_onboarding_transport(
        _dm_from(STRANGER, surface),
        uow_factory=_uow_factory(),
        adapters=SurfacePlatformAdapterRegistry(),
    )
    assert transport is None


async def test_a_colleague_mentioning_the_teammate_in_its_channel_is_answered(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    surface = await _managed_teammate(
        authenticated_client, db_session, fixed_test_user, fake_slack, test_pod["id"]
    )
    # The manager hands the channel over, which makes the hand-off's person
    # its owner: who answers for the people outside the pod in it.
    await _invite_and_pick(fake_slack, message_store, surface, "C-PUBLIC")
    fake_slack.user_overrides[STRANGER] = {"email": "colleague@elsewhere.test"}
    request = _dm_from(
        STRANGER, surface, text=f"<@{TEAMMATE_BOT}> what's on the roadmap?"
    )
    event = request.payload["event"]
    event.update(
        {"type": "app_mention", "channel": "C-PUBLIC", "channel_type": "channel"}
    )

    context = await _prepare(db_session, request)

    assert isinstance(context, SurfaceChatContext), context
    # Answered as the public is, on the channel owner's behalf -- not as a member.
    assert context.audience.answers_outsiders
    assert str(context.user_id) == fixed_test_user["id"]


async def test_the_group_page_says_who_in_the_channel_is_whose(
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

    surface = await _managed_teammate(
        authenticated_client, db_session, fixed_test_user, fake_slack, test_pod["id"]
    )
    await _invite_and_pick(fake_slack, message_store, surface, "C-PUBLIC")
    fake_slack.channel_members["C-PUBLIC"] = [
        "U-ME",
        STRANGER,
        GUEST,
        "U-OTHERCO",
        TEAMMATE_BOT,
    ]
    fake_slack.user_overrides.update(
        {
            "U-ME": {
                "email": "me@elsewhere.test",
                "display_name": "Me",
                "team_id": TEAM,
            },
            STRANGER: {
                "email": "c@elsewhere.test",
                "display_name": "Colleague",
                "team_id": TEAM,
            },
            GUEST: {
                "email": "g@agency.test",
                "display_name": "Guest",
                "is_restricted": True,
                "team_id": TEAM,
            },
            "U-OTHERCO": {
                "email": "o@other.test",
                "display_name": "Partner",
                "team_id": "T-OTHER",
            },
            TEAMMATE_BOT: {"display_name": "Teammate", "is_bot": True, "team_id": TEAM},
        }
    )
    async with _uow_factory()() as uow:
        await SlackManagerRepository(uow.session).link_person(
            team_id=TEAM, slack_user_id="U-ME", user_id=UUID(fixed_test_user["id"])
        )
        await uow.commit()
    groups = await authenticated_client.get(f"/pods/{test_pod['id']}/groups")
    group = next(
        g for g in groups.json()["items"] if g["external_channel_id"] == "C-PUBLIC"
    )

    response = await authenticated_client.get(
        f"/pods/{test_pod['id']}/groups/{group['id']}/slack-people"
    )

    assert response.status_code == 200, response.text
    standings = {item["name"]: item["standing"] for item in response.json()["items"]}
    assert standings == {
        "Me": "in",
        "Colleague": "workspace",
        "Guest": "outside",
        "Partner": "outside",
    }


async def test_a_big_channel_says_how_many_more_it_did_not_look_at(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    surface = await _managed_teammate(
        authenticated_client, db_session, fixed_test_user, fake_slack, test_pod["id"]
    )
    await _invite_and_pick(fake_slack, message_store, surface, "C-PUBLIC")
    fake_slack.channel_members["C-PUBLIC"] = [TEAMMATE_BOT] + [
        f"U-{n:03d}" for n in range(57)
    ]
    groups = await authenticated_client.get(f"/pods/{test_pod['id']}/groups")
    group = next(
        g for g in groups.json()["items"] if g["external_channel_id"] == "C-PUBLIC"
    )

    response = await authenticated_client.get(
        f"/pods/{test_pod['id']}/groups/{group['id']}/slack-people"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 50
    assert body["more"] == 7
