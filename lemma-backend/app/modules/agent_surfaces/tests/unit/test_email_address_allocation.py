"""The address people will type to reach an agent.

Readable addresses are the point — they go in the UI and get typed by hand —
and readability is exactly what makes two of them collide. These pin the shape,
the length budget, and what happens on a clash.
"""

from __future__ import annotations

from app.modules.agent_surfaces.services.email_address_allocation import (
    MAX_LOCAL_PART,
    build_agent_email,
    build_local_part,
    candidate_addresses,
    slugify,
)


def test_the_address_reads_as_the_agent_and_the_pod():
    assert (
        build_agent_email(
            agent_name="Ops Assistant", pod_name="Acme Corp", domain="ops.asur.work"
        )
        == "ops-assistant.acme-corp@ops.asur.work"
    )


def test_punctuation_and_case_are_normalised_away():
    assert slugify("  Support & Triage!  ") == "support-triage"
    assert slugify("") == "agent"


def test_the_local_part_never_exceeds_the_rfc_limit():
    """Over 64 octets and the address is rejected or truncated downstream —
    either way the reply never comes back."""
    local = build_local_part(agent_name="a" * 40, pod_name="b" * 60)

    assert len(local) <= MAX_LOCAL_PART


def test_the_agent_half_survives_when_the_budget_is_tight():
    """The pod slug is truncated first: the agent name is what identifies it."""
    local = build_local_part(agent_name="support-triage", pod_name="b" * 90)

    assert local.startswith("support-triage.")
    assert len(local) <= MAX_LOCAL_PART


def test_a_pathologically_long_agent_name_still_yields_a_valid_address():
    local = build_local_part(agent_name="a" * 200, pod_name="acme")

    assert len(local) <= MAX_LOCAL_PART
    assert "@" not in local


def test_the_plain_address_is_offered_before_any_suffixed_one():
    candidates = candidate_addresses(
        agent_name="Ops", pod_name="Acme", domain="ops.asur.work"
    )

    assert candidates[0] == "ops.acme@ops.asur.work"
    assert len(candidates) > 1
    assert len(set(candidates)) == len(candidates), "suffixes must differ"


def test_suffixed_candidates_stay_within_the_limit_too():
    for address in candidate_addresses(
        agent_name="a" * 50, pod_name="b" * 50, domain="ops.asur.work"
    ):
        assert len(address.split("@")[0]) <= MAX_LOCAL_PART
