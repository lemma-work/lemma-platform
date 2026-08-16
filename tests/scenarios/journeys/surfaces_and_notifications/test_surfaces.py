"""Surfaces and notifications → connecting a pod to a platform."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Connect a pod to a platform"),
]


@pytest.fixture
async def pod(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    return alice, await alice.creates_a_pod()


@scenario("A person sees which platforms they can connect")
@proves("PS-SURF-001")
@covers("agent.surface.available", "agent.surface.list")
async def test_available_platforms_are_listed(pod):
    alice, the_pod = pod

    available = await alice.platforms_available_to(the_pod)

    assert isinstance(available, list), available
    assert await alice.surfaces_in(the_pod) == [], (
        "a new pod is connected to nothing"
    )


@scenario("A person is told what a platform needs before they start")
@proves("PS-SURF-002")
@covers("agent.surface.setup_guide")
async def test_a_setup_guide_is_available(pod):
    alice, the_pod = pod

    guide = await alice.setup_guide_for("slack", in_pod=the_pod)

    assert guide is not None


@scenario("A surface with no usable configuration is refused, not half-created")
@proves("PS-SURF-001")
@covers("agent.surface.create", "agent.surface.list")
async def test_an_unconfigured_surface_is_refused(pod):
    alice, the_pod = pod

    await alice.is_refused_connecting_a_surface(
        in_pod=the_pod, platform="slack", config={}
    )

    assert await alice.surfaces_in(the_pod) == [], (
        "a refused connection must leave nothing behind"
    )


@scenario("A person sees the surfaces they can be reached on")
@proves("PS-SURF-023")
@covers("agent.surface.list_mine")
async def test_my_surfaces_are_listable(pod):
    alice, _the_pod = pod

    mine = await alice.my_surfaces()

    assert isinstance(mine, list), mine


@scenario("Someone outside the pod cannot see or connect its surfaces")
@proves("PS-SURF-001")
@covers("agent.surface.list", "agent.surface.create")
async def test_an_outsider_cannot_touch_surfaces(world, pod):
    alice, the_pod = pod
    outsider = await world.new_person("outsider")

    listed = await outsider.api.call("GET", f"/pods/{the_pod['id']}/surfaces")
    created = await outsider.api.call(
        "POST", f"/pods/{the_pod['id']}/surfaces",
        json={"platform": "slack", "name": "trespass"},
    )

    assert listed.status_code >= 400, listed.status_code
    assert created.status_code >= 400, created.status_code


@scenario("A platform's verification challenge is answered without a session")
@proves("PS-SURF-010")
@covers("surface.webhook.verify")
async def test_webhook_verification_needs_no_session(world, pod):
    alice, _the_pod = pod

    # No Authorization header at all: a platform cannot sign in.
    anonymous = await world.new_person("anonymous", sign_up=False)
    response = await anonymous.api.call("GET", "/surfaces/webhooks/slack")

    assert response.status_code != 401, (
        "a platform verifying its webhook cannot authenticate; demanding a "
        f"session makes setup impossible (got {response.status_code})"
    )


@scenario("An unsigned webhook payload is rejected")
@proves("PS-SURF-010")
@covers("surface.webhook.handle_platform")
async def test_an_unsigned_webhook_is_rejected(world, pod):
    anonymous = await world.new_person("anonymous", sign_up=False)

    response = await anonymous.api.call(
        "POST", "/surfaces/webhooks/slack", json={"type": "event_callback", "event": {}}
    )

    assert response.status_code >= 400, (
        "an unsigned payload claiming to be from Slack must not be acted on "
        f"(got {response.status_code})"
    )
