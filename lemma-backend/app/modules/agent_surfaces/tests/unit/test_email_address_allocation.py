"""The address people will type to reach an agent.

Readable addresses are the point — they go in the UI and get typed by hand —
and readability is exactly what makes two of them collide. These pin the shape,
the length budget, and what happens on a clash.
"""

from __future__ import annotations

from app.modules.agent_surfaces.services.email_address_allocation import (
    MAX_LOCAL_PART,
    RESERVED_LOCAL_PARTS,
    build_agent_email,
    build_local_part,
    candidate_addresses,
    is_reserved,
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


# ------------------------------------------------- the pod's own mailbox


def test_the_pod_assistant_is_addressed_by_the_pod_alone():
    """It is not one agent among several — it is the pod answering.

    ``acme.acme@`` would be the mechanical result of treating the assistant as
    an agent named after its pod, and it reads like a mistake. This is also the
    shortest address a person can be asked to type.
    """
    assert (
        build_agent_email(
            agent_name=None, pod_name="Acme Ops", domain="ops.example.com"
        )
        == "acme-ops@ops.example.com"
    )


def test_the_pod_mailbox_still_takes_a_suffix_when_the_name_is_taken():
    """Readable means collidable; the unique index is still the arbiter."""
    address = build_agent_email(
        agent_name=None, pod_name="Acme Ops", domain="ops.example.com", suffix="k3p9"
    )

    assert address == "acme-ops-k3p9@ops.example.com"


def test_a_pod_with_no_name_still_gets_a_valid_address():
    """A nameless pod is a data problem, not a reason to mint a broken address."""
    assert (
        build_agent_email(agent_name=None, pod_name=None, domain="ops.example.com")
        == "pod@ops.example.com"
    )


def test_a_very_long_pod_name_is_cut_to_the_rfc_limit():
    local = build_local_part(agent_name=None, pod_name="x" * 200, suffix="k3p9")

    assert len(local) <= MAX_LOCAL_PART
    assert local.endswith("-k3p9")


# ------------------------------------------------- addresses nobody may hold


def test_a_pod_named_after_a_role_address_never_gets_the_bare_form():
    """The assistant's shape is the only one that can produce ``postmaster@``.

    The inbound domain is one catch-all shared by every organization, so this is
    not the pod's address to take: mail systems and abuse desks expect a person
    behind it, and the first tenant to name a pod "Postmaster" would be the one
    reading their bounce traffic.
    """
    candidates = candidate_addresses(
        agent_name=None, pod_name="Postmaster", domain="ops.example.com", attempts=5
    )

    assert "postmaster@ops.example.com" not in candidates
    assert all(address.startswith("postmaster-") for address in candidates)
    assert len(candidates) == 5, "dropping it must not cost a retry"
    assert len(set(candidates)) == len(candidates)


def test_an_ordinary_pod_name_is_still_offered_plain_first():
    """The guard is a denylist, not a policy of suffixing everything."""
    candidates = candidate_addresses(
        agent_name=None, pod_name="Acme Ops", domain="ops.example.com"
    )

    assert candidates[0] == "acme-ops@ops.example.com"


def test_an_agent_inside_a_role_named_pod_is_left_alone():
    """``triage.support@`` is nobody's role address, so nothing should stop it."""
    candidates = candidate_addresses(
        agent_name="Triage", pod_name="Support", domain="ops.example.com"
    )

    assert candidates[0] == "triage.support@ops.example.com"


def test_reserved_matches_the_whole_local_part_not_a_prefix():
    assert is_reserved("support")
    assert is_reserved("  Postmaster  ")
    assert not is_reserved("triage.support")
    assert not is_reserved("support-triage.acme")


def test_the_rfc_2142_roles_are_all_covered():
    """The ones a mail system or an abuse desk expects a human behind."""
    assert {"postmaster", "abuse", "hostmaster", "webmaster", "security"} <= (
        RESERVED_LOCAL_PARTS
    )


def test_a_reserved_role_cannot_be_reached_by_respelling_it():
    """`postmaster@` was refused and `post-master@` was not.

    Pod names admit spaces, hyphens and underscores, and `slugify` turns all
    three into a hyphen. So the guard screened the one spelling nobody would
    type and let through the ones they would — on a domain every organization
    on the deployment shares.
    """
    for spelling in ("Post Master", "Post-Master", "Post_Master", "POST  MASTER"):
        first = candidate_addresses(
            agent_name=None, pod_name=spelling, domain="ops.example.com"
        )[0]
        assert first != "post-master@ops.example.com", (
            f"{spelling!r} still reaches the role address as {first!r}"
        )
        assert first.startswith("post-master-")


def test_lemma_s_own_voice_is_not_claimable_by_a_tenant():
    """The second thing the list is for: "this is Lemma talking, not a tenant".

    `lemma@` was refused, but a pod named "Lemma Support" took
    `lemma-support@` — which is precisely the address a person reads as Lemma's
    own, on the shared inbound domain.
    """
    for name in ("Lemma Support", "Lemma Billing", "Lemma Security", "Lemma Team"):
        first = candidate_addresses(
            agent_name=None, pod_name=name, domain="ops.example.com"
        )[0]
        plain = f"{slugify(name)}@ops.example.com"
        assert first != plain, f"{name!r} still claims {plain!r}"


def test_a_pod_honestly_named_lemma_still_gets_a_mailbox():
    """The reason these are entries and not a `lemma*` prefix rule.

    Every candidate is screened, so a prefix rule would reject the suffixed
    forms too — `lemma-7rmt@` starts with `lemma-` just as `lemma-support@`
    does. The bounded generation loop would run out and return nothing, and the
    pod would end up with no address at all: worse than an ugly one, which is
    the trade this whole module exists to avoid.
    """
    candidates = candidate_addresses(
        agent_name=None, pod_name="Lemma", domain="ops.example.com"
    )

    assert len(candidates) == 5, f"the generator starved: {candidates}"
    assert all(c.startswith("lemma-") for c in candidates)
    assert "lemma@ops.example.com" not in candidates


def test_the_dot_is_left_alone_because_it_separates_rather_than_spells():
    """Normalizing it away would refuse addresses nobody misreads.

    An agent named "Sale" in a pod named "S" is `sale.s@`. Collapsing the dot
    makes that `sales`, a reserved role — but the dot is the agent/pod
    separator, and a local part carrying one is an agent's address rather than
    a bare role word.
    """
    assert not is_reserved("sale.s")
    assert not is_reserved("triage.support")
    assert is_reserved("sales")
