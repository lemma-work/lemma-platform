"""Agents and conversations → choosing which model an agent uses."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.credentials import needs
from harness.environment import OPEN_SIGNUP

#: A real, resolvable public host. The platform refuses a base URL it cannot
#: resolve — the same guard that stops a connector pointing at internal
#: services — so a made-up domain is rejected before anything is stored.
#: Nothing here ever calls it: creating a profile records configuration, and
#: the scenarios assert on what comes back from Lemma, not from a provider.
PROVIDER_BASE_URL = "https://api.openai.com/v1"

pytestmark = [
    journey("Agents and conversations"),
    capability("Choose which model an agent uses"),
]


@pytest.fixture
async def org(world):
    """Priya, who owns Vantage Freight.

    An organization's model providers are organization administration, so this
    one needs the owner rather than the editor who runs the pods. Daniel would
    be refused for want of `org.update`, and the scenario would be reporting a
    missing permission as though it were the behaviour under test.
    """
    priya = await world.person("priya")
    return priya, priya.organization

@scenario("An organization configures its own model provider")
@proves("PS-AGENT-004")
@covers("agent.runtime.profiles.create", "agent.runtime.profiles.get",
        "agent.runtime.profiles.list")
async def test_an_organization_can_add_a_provider(org, run):
    alice, organization = org

    created = await alice.api.post(
        f"/organizations/{organization['id']}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": run.name("our-own-gateway"),
            "base_url": PROVIDER_BASE_URL,
            "api_key": "org-provider-key",
            "model_names": ["house-model"],
            "default_model_name": "house-model",
        },
    )

    reopened = await alice.opens_runtime_profile(
        created["id"], in_organization=organization
    )
    assert reopened["name"] == created["name"], reopened
    listed = {p["id"] for p in await alice.runtime_profiles_in(organization)}
    assert created["id"] in listed, listed


@scenario("A provider's credential is never handed back")
@proves("PS-AGENT-004", "PS-CONN-011")
@covers("agent.runtime.profiles.get", "agent.runtime.profiles.list")
async def test_a_provider_key_is_never_returned(org, run):
    alice, organization = org
    created = await alice.api.post(
        f"/organizations/{organization['id']}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": run.name("secret-gateway"),
            "base_url": PROVIDER_BASE_URL,
            "api_key": "super-secret-provider-key",
            "model_names": ["house-model"],
        },
    )

    reopened = await alice.opens_runtime_profile(
        created["id"], in_organization=organization
    )
    listed = await alice.runtime_profiles_in(organization)

    assert "super-secret-provider-key" not in str(reopened), reopened
    assert "super-secret-provider-key" not in str(listed), (
        "a provider credential must never come back, at any privilege level"
    )
    assert reopened.get("has_credentials") is True, (
        f"the profile should still say it holds one: {reopened}"
    )


@scenario("A provider can be renamed, archived, and brought back")
@proves("PS-AGENT-004")
@covers("agent.runtime.profiles.update", "agent.runtime.profiles.archive",
        "agent.runtime.profiles.restore")
async def test_a_provider_can_be_archived_and_restored(org, run):
    alice, organization = org
    created = await alice.api.post(
        f"/organizations/{organization['id']}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": run.name("temporary-gateway"),
            "base_url": PROVIDER_BASE_URL,
            "api_key": "k",
            "model_names": ["house-model"],
        },
    )
    profile_id = created["id"]
    base = f"/organizations/{organization['id']}/agent-runtime/profiles/{profile_id}"

    renamed = await alice.api.patch(
        base, json={"source": "OPENAI_COMPATIBLE", "name": run.name("renamed-gateway")}
    )
    assert renamed["name"].startswith("renamed-gateway"), renamed

    await alice.api.delete(base)
    archived = await alice.opens_runtime_profile(profile_id, in_organization=organization)
    assert str(archived["status"]).upper() != "ACTIVE", archived

    await alice.api.post(f"{base}:restore")
    restored = await alice.opens_runtime_profile(profile_id, in_organization=organization)
    assert str(restored["status"]).upper() == "ACTIVE", restored


@scenario("Someone outside the organization cannot add a provider")
@proves("PS-AGENT-004")
@covers("agent.runtime.profiles.create")
async def test_an_outsider_cannot_add_a_provider(world, org, run):
    _alice, organization = org
    # Somebody in no organization at all, which is what this promise is about.
    # None of the standing cast is that, so this one stays a fresh person.
    needs(OPEN_SIGNUP)
    outsider = await world.new_person("outsider")

    response = await outsider.api.call(
        "POST", f"/organizations/{organization['id']}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE", "name": run.name("trespass"),
            "base_url": PROVIDER_BASE_URL, "api_key": "k",
            "model_names": ["m"],
        },
    )

    assert response.status_code >= 400, response.status_code
