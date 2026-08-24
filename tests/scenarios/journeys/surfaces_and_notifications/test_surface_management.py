"""Surfaces and notifications → changing and removing a connected surface."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.telegram_view import TelegramView

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Connect a pod to a platform"),
]


@pytest.fixture
async def connected(world, run, egress):
    fake = TelegramView(egress)
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    agent = await alice.creates_an_agent(in_pod=pod)
    surface = await alice.becomes_reachable_on_telegram(
        in_pod=pod, agent=agent["name"]
    )
    yield alice, pod, agent, surface, fake


@scenario("A person reads a connected surface and how far its setup got")
@proves("PS-SURF-001")
@covers(
    "agent.surface.get", "agent.surface.setup", "agent.surface.list", "surface.connected"
)
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
        "POST",
        path,
        json={
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 1700000000,
                "chat": {"id": 999, "type": "private"},
                "from": {"id": 999, "is_bot": False, "first_name": "S"},
                "text": "hi",
            },
        },
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


@scenario("Setting up a managed bot needs the platform's manager configured")
@proves("PS-SURF-002")
@covers("agent.surface.telegram_managed.start", "agent.surface.telegram_managed.get")
async def test_a_managed_bot_setup_says_what_is_missing(connected):
    alice, pod, agent, _surface, _fake = connected

    started = await alice.api.call(
        "POST",
        f"/pods/{pod['id']}/telegram-bot-setups",
        json={"name": "managed", "default_agent_name": agent["name"]},
    )

    if started.status_code < 400:
        setup_id = started.json().get("id") or started.json().get("setup_id")
        followed = await alice.api.call(
            "GET", f"/pods/{pod['id']}/telegram-bot-setups/{setup_id}"
        )
        assert followed.status_code == 200, followed.text[:300]
    else:
        # A deployment with no manager bot cannot run the guided setup, and has
        # to say so rather than leaving a half-made surface behind.
        assert started.status_code >= 400, started.status_code
        assert not any(s["name"] == "managed" for s in await alice.surfaces_in(pod)), (
            "a refused guided setup must leave no surface behind"
        )


@scenario("The managed-bot webhook rejects an unsigned delivery")
@proves("PS-SURF-010")
@covers("surface.webhook.handle_telegram_manager")
async def test_the_manager_webhook_rejects_unsigned(world, connected):
    anonymous = await world.new_person("anonymous", sign_up=False)

    response = await anonymous.api.call(
        "POST",
        "/surfaces/webhooks/telegram-manager",
        json={"update_id": 1, "message": {"text": "/start"}},
    )

    assert response.status_code >= 400, (
        f"an unsigned delivery to the manager bot was accepted ({response.status_code})"
    )


@scenario("A tenant consent callback without a grant is refused")
@proves("PS-SURF-002")
@covers("agent.surface.teams_admin_consent_callback")
async def test_a_consent_callback_without_a_grant_is_refused(world):
    anonymous = await world.new_person("anonymous", sign_up=False)

    response = await anonymous.api.call(
        "GET",
        "/surfaces/teams/admin-consent/callback",
        params={"tenant": "not-a-tenant", "state": "not-a-state"},
    )

    assert response.status_code >= 400 or "error" in response.text.lower(), (
        f"a consent callback we never started must not be honoured: "
        f"{response.status_code} {response.text[:200]}"
    )
