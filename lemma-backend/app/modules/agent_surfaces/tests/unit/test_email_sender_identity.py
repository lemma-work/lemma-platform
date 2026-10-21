"""What an agent's email says in its ``From`` display name.

One name, so what is worth pinning is that it stays one — the shape that was
reported ("Lem (Ada Member) via Lemma") had three parts, and two of them were
already somewhere else: the product on the sending domain, the person in the
body's ``On behalf of`` header.
"""

from __future__ import annotations

from email.utils import formataddr, getaddresses

from app.modules.agent_surfaces.platforms.email_sender_identity import (
    MAX_DISPLAY_NAME,
    sender_display_name,
)


def test_the_pods_own_assistant_goes_out_as_the_pod():
    """The reported bug, from the other end: ``Lem (Ada Member) via Lemma``.

    The caller resolves this name — ``group_names.sender_name_for`` — and this
    is what it hands over for a pod's mailbox.
    """
    assert sender_display_name(sender_name="Sales", product_name="Lemma") == "Sales"


def test_a_named_agent_goes_out_as_its_own_name():
    assert sender_display_name(sender_name="Priya", product_name="Lemma") == "Priya"


def test_a_send_that_knows_no_sender_says_the_deployment():
    assert sender_display_name(sender_name=None, product_name="Lemma") == "Lemma"


def test_a_self_hosted_deployment_brands_its_own_fallback():
    """``product_name`` is the deployment's own RESEND_FROM_NAME, not a constant."""
    assert sender_display_name(sender_name=None, product_name="Acme") == "Acme"


def test_an_absurd_name_is_cut_rather_than_replaced_by_the_product():
    """A clipped agent name still says which agent; "Lemma" says nothing.

    255 characters is what the agent API accepts, so this is reachable input
    rather than a hypothetical.
    """
    composed = sender_display_name(sender_name="x" * 255, product_name="Lemma")
    assert len(composed) == MAX_DISPLAY_NAME
    assert composed.startswith("xxxx")


def test_newlines_in_a_name_cannot_reach_the_header():
    """Header injection's other door: an agent name is free text from the API."""
    composed = sender_display_name(
        sender_name="Priya\r\nBcc: evil@example.com", product_name="Lemma"
    )
    assert "\r" not in composed and "\n" not in composed
    assert composed == "Priya Bcc: evil@example.com"


def test_a_hostile_name_survives_formataddr_as_an_inert_display_name():
    """The composed name is quoted by the caller, not sanitised here.

    An agent called ``x <evil@example.com>`` must not become a second address,
    and one with a comma must not split into two. This is the contract the
    Resend service relies on by calling ``formataddr`` rather than an f-string.
    """
    composed = sender_display_name(
        sender_name="x <evil@example.com>, Ops", product_name="Lemma"
    )
    header = formataddr((composed, "priya.acme@updates.lemma.work"))
    parsed = getaddresses([header])
    assert len(parsed) == 1
    assert parsed[0][1] == "priya.acme@updates.lemma.work"
