"""What an agent's email says in its ``From`` display name.

The rules worth pinning are the degradations, not the happy path: every one of
them exists because a form that fits is worth more than a form that is complete,
and the ordering (agent, then person, then product) is what decides which half
survives a mail client's truncation.
"""

from __future__ import annotations

from email.utils import formataddr, getaddresses

from app.modules.agent_surfaces.platforms.email_sender_identity import (
    MAX_DISPLAY_NAME,
    sender_display_name,
)


def test_all_three_parts_in_priority_order():
    assert (
        sender_display_name(
            agent_name="Priya",
            actor_display_name="Deepak Jha",
            product_name="Lemma",
        )
        == "Priya (Deepak Jha) via Lemma"
    )


def test_no_human_behind_the_run_leaves_the_agent_and_the_product():
    """A schedule or a trigger fired this; there is nobody to name."""
    assert (
        sender_display_name(
            agent_name="Priya", actor_display_name=None, product_name="Lemma"
        )
        == "Priya via Lemma"
    )


def test_an_email_address_is_never_shown_as_the_person():
    """``get_user_display_name`` falls back to the email when a name is unset.

    Right for the body header, which promises "on behalf of <someone>". Wrong
    here: an unrelated address inside a From display name is what both a spam
    filter and a person read as forgery, so the actor is dropped instead.
    """
    assert (
        sender_display_name(
            agent_name="Priya",
            actor_display_name="deepak@gmail.com",
            product_name="Lemma",
        )
        == "Priya via Lemma"
    )


def test_an_agentless_surface_does_not_repeat_the_product():
    """``_egress_metadata_with_agent_name`` reports "Lemma" when there is no agent.

    Passing that through composes "Lemma (Deepak Jha) via Lemma".
    """
    assert (
        sender_display_name(
            agent_name="Lemma",
            actor_display_name="Deepak Jha",
            product_name="Lemma",
        )
        == "Deepak Jha via Lemma"
    )


def test_nothing_known_is_what_the_header_said_before_any_of_this():
    assert (
        sender_display_name(
            agent_name=None, actor_display_name=None, product_name="Lemma"
        )
        == "Lemma"
    )


def test_a_self_hosted_deployment_brands_the_third_slot():
    """``product_name`` is the deployment's own RESEND_FROM_NAME, not a constant."""
    assert (
        sender_display_name(
            agent_name="Priya", actor_display_name="Deepak Jha", product_name="Acme"
        )
        == "Priya (Deepak Jha) via Acme"
    )


def test_a_long_pair_degrades_to_a_first_name_before_it_degrades_further():
    """The person is worth keeping; their surname is what the budget buys."""
    composed = sender_display_name(
        agent_name="Support Triage Escalations",
        actor_display_name="Bartholomew Fotheringay-Smythe",
        product_name="Lemma",
    )
    assert composed == "Support Triage Escalations (Bartholomew) via Lemma"
    assert len(composed) <= MAX_DISPLAY_NAME


def test_two_long_names_drop_the_person_rather_than_cutting_mid_word():
    """Not a truncation of the full form: a clipped name reads as a real one."""
    composed = sender_display_name(
        agent_name="Customer Escalations And Billing Assistant",
        actor_display_name="Bartholomew Fotheringay-Smythe",
        product_name="Lemma",
    )
    assert composed == "Customer Escalations And Billing Assistant via Lemma"
    assert len(composed) <= MAX_DISPLAY_NAME


def test_an_absurd_agent_name_is_cut_rather_than_replaced_by_the_product():
    """A clipped agent name still says which agent; "Lemma" says nothing.

    255 characters is what the agent API accepts, so this is reachable input
    rather than a hypothetical.
    """
    composed = sender_display_name(
        agent_name="x" * 255, actor_display_name="Deepak Jha", product_name="Lemma"
    )
    assert len(composed) <= MAX_DISPLAY_NAME
    assert composed.startswith("xxxx")
    assert composed.endswith(" via Lemma")


def test_newlines_in_a_name_cannot_reach_the_header():
    """Header injection's other door: an agent name is free text from the API."""
    composed = sender_display_name(
        agent_name="Priya\r\nBcc: evil@example.com",
        actor_display_name=None,
        product_name="Lemma",
    )
    assert "\r" not in composed and "\n" not in composed
    assert composed == "Priya Bcc: evil@example.com via Lemma"


def test_a_hostile_name_survives_formataddr_as_an_inert_display_name():
    """The composed name is quoted by the caller, not sanitised here.

    An agent called ``x <evil@example.com>`` must not become a second address,
    and one with a comma must not split into two. This is the contract the
    Resend service relies on by calling ``formataddr`` rather than an f-string.
    """
    composed = sender_display_name(
        agent_name="x <evil@example.com>, Ops",
        actor_display_name=None,
        product_name="Lemma",
    )
    header = formataddr((composed, "priya.acme@updates.lemma.work"))
    parsed = getaddresses([header])
    assert len(parsed) == 1
    assert parsed[0][1] == "priya.acme@updates.lemma.work"
