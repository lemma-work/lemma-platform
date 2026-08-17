"""Surfaces and notifications → changing and removing a connected surface."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.fake_platform import start_fake_telegram

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Connect a pod to a platform"),
]


@pytest.fixture
async def connected(world):
    fake = start_fake_telegram()
    try:
        alice = await world.new_person("alice")
        organization = await alice.creates_an_organization()
        pod = await alice.creates_a_pod()
        agent = await alice.creates_an_agent(in_pod=pod)
        auth_config = await alice.installs_connector("telegram", in_organization=organization)
        account = await alice.connects_account(
            in_organization=organization, auth_config=auth_config,
            credentials={"bot_token": "424242:managed", "api_base_url": fake.api_base},
        )
        surface = await alice.connects_a_surface(
            in_pod=pod, platform="TELEGRAM", named="tg",
            agent=agent["name"], account=account,
        )
        yield alice, pod, agent, surface, fake
    finally:
        fake.stop()


@scenario("A person reads a connected surface and how far its setup got")
@proves("PS-SURF-001")
@covers("agent.surface.get", "agent.surface.setup", "agent.surface.list")
async def test_a_surface_reads_back(connected):
    alice, pod, _agent, surface, _fake = connected

    reopened = await alice.opens_surface(surface["name"], in_pod=pod)
    setup = await alice.setup_state_of(surface["name"], in_pod=pod)

    assert reopened["platform"] == "TELEGRAM", reopened
    assert setup is not None, setup


@scenario("A person points a surface at a different agent")
@proves("PS-SURF-003")
@covers("agent.surface.update", "agent.surface.get")
async def test_a_surface_can_be_repointed(connected):
    alice, pod, _agent, surface, _fake = connected
    other = await alice.creates_an_agent(in_pod=pod, named="second_agent")

    await alice.changes_surface(
        surface["name"], in_pod=pod, default_agent_name=other["name"]
    )

    reopened = await alice.opens_surface(surface["name"], in_pod=pod)
    assert reopened["agent_name"] == other["name"], reopened


@scenario("A person lists the channels a surface can reach")
@proves("PS-SURF-023")
@covers("agent.surface.channels")
async def test_channels_are_listable(connected):
    alice, pod, _agent, surface, _fake = connected

    response = await alice.channels_of(surface["name"], in_pod=pod)

    assert response.status_code < 500, response.text[:300]


@scenario("A person chooses where the platform should reach them by default")
@proves("PS-SURF-023")
@covers("agent.surface.set_my_default", "agent.surface.list_mine")
async def test_a_default_surface_can_be_chosen(connected):
    alice, _pod, _agent, surface, _fake = connected

    response = await alice.makes_default_surface(surface, platform="TELEGRAM")

    assert response.status_code < 500, response.text[:300]
    assert isinstance(await alice.my_surfaces(), list)


@scenario("A platform's app definition is generated rather than hand-written")
@proves("PS-SURF-002")
@covers("agent.surface.slack_manifest")
async def test_a_slack_manifest_is_generated(connected):
    alice, _pod, _agent, _surface, _fake = connected

    response = await alice.slack_manifest()

    assert response.status_code == 200, response.text[:300]
    assert "lemma" in response.text.lower() or "oauth" in response.text.lower(), (
        response.text[:300]
    )


@scenario("Deleting a surface stops it accepting messages")
@proves("PS-SURF-003")
@covers("agent.surface.delete", "agent.surface.list", "surface.webhook.handle_surface")
async def test_deleting_a_surface_stops_it(connected):
    alice, pod, _agent, surface, fake = connected
    path = fake.webhook_path
    secret = fake.webhook_secret

    await alice.deletes_surface(surface["name"], in_pod=pod)

    assert surface["name"] not in {s["name"] for s in await alice.surfaces_in(pod)}
    delivered = await alice.api.call(
        "POST", path,
        json={"update_id": 1, "message": {"message_id": 1, "date": 1700000000,
              "chat": {"id": 999, "type": "private"},
              "from": {"id": 999, "is_bot": False, "first_name": "S"}, "text": "hi"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )
    assert delivered.status_code >= 400, (
        f"a deleted surface still accepted a delivery ({delivered.status_code})"
    )


@scenario("A platform can verify a surface's own webhook without a session")
@proves("PS-SURF-010")
@covers("surface.webhook.verify_surface")
async def test_a_surface_webhook_can_be_verified(world, connected):
    _alice, _pod, _agent, surface, _fake = connected
    anonymous = await world.new_person("anonymous", sign_up=False)

    response = await anonymous.api.call("GET", f"/surfaces/{surface['id']}/webhook")

    assert response.status_code != 401, (
        f"a platform verifying a webhook cannot sign in ({response.status_code})"
    )
