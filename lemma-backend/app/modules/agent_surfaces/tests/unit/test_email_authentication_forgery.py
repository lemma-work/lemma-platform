"""Text the sender wrote must never be read as the receiver's verdict.

``test_email_sender_authentication`` covers the shapes a well-behaved receiver
produces. This file covers the adversarial ones, because half of what an
``Authentication-Results`` header *contains* belongs to the sender: the envelope
``MAIL FROM`` is echoed into ``smtp.mailfrom=`` and, on most receivers, into a
comment beside it. Every case here returned ``PASS`` before the parser was
rewritten, and each one is an account takeover -- an inbound address resolves to
a Lemma user, and the agent then runs with that user's authority.

The dict cases are not a variant. They are what Resend actually sends: the
webhook carries no headers at all, so the verdict comes from
``GET /emails/receiving/{id}``, whose ``headers`` is a dict whose values are
sometimes lists and sometimes a JSON array inside a string. Every pre-existing
test used the ``[{name, value}]`` list shape, which Resend does not send -- so
the production path had no coverage.
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.platforms.email_authentication import (
    EmailAuthenticationVerdict,
    authentication_results_values,
    evaluate_email_authentication,
)

pytestmark = pytest.mark.unit

VICTIM = "ceo@corp.com"
RECEIVER = frozenset({"mx.google.com"})


def _list(*values: str) -> list[dict[str, str]]:
    """The ``[{name, value}]`` shape, one entry per header occurrence."""
    return [{"name": "Authentication-Results", "value": value} for value in values]


def _verdict(headers, *, sender: str = VICTIM, trusted=None):
    return evaluate_email_authentication(
        headers, from_address=sender, trusted_authserv_ids=trusted
    )


# ── Comments are the sender's text, and must not become the receiver's ──────


def test_a_quoted_paren_in_the_envelope_sender_cannot_end_the_comment_early():
    """The attack that survived a trusted receiver's own header.

    A quoted local part may contain ``)``. The old stripper's
    ``\\([^()]*\\)`` ended the comment there, exposing the rest -- including a
    ``dmarc=pass`` the sender chose -- as if the receiver had written it. The
    real verdict here is an SPF pass for ``evil.com``, unaligned with the From
    domain, which authenticates nothing.
    """
    header = (
        'mx.google.com; spf=pass (google.com: domain of "a) dmarc=pass (b"@evil.com '
        'designates 1.2.3.4) smtp.mailfrom="a) dmarc=pass (b"@evil.com'
    )
    assert _verdict(_list(header), trusted=RECEIVER) is not (
        EmailAuthenticationVerdict.PASS
    )


def test_a_nested_comment_cannot_smuggle_a_verdict_past_the_stripper():
    """One regex pass over ``(a (b) c)`` leaves ``(a  c)`` behind.

    Whatever sat between the inner and outer parens then read as top-level
    text. Here the receiver actually said ``spf=fail``.
    """
    header = "mx.google.com; (note (x) dmarc=pass) spf=fail smtp.mailfrom=evil.com"
    assert _verdict(_list(header), trusted=RECEIVER) is EmailAuthenticationVerdict.FAIL


def test_an_unbalanced_quote_inside_a_comment_fails_closed():
    """Swallowing the rest of the header is safe; believing it is not."""
    header = 'mx.google.com; spf=fail (domain of "unterminated dmarc=pass'
    assert _verdict(_list(header)) is not EmailAuthenticationVerdict.PASS


# ── One method, one answer ──────────────────────────────────────────────────


def test_a_repeated_dmarc_result_is_tampering_not_a_later_correction():
    """DMARC is evaluated once, so two answers means somebody added one.

    The old dict comprehension was last-wins, so appending ``dmarc=pass`` to a
    real ``dmarc=fail`` flipped the verdict.
    """
    header = "mx.google.com; dmarc=fail; dmarc=pass"
    assert _verdict(_list(header)) is EmailAuthenticationVerdict.FAIL


def test_the_order_of_a_conflicting_dmarc_pair_does_not_change_the_answer():
    header = "mx.google.com; dmarc=pass; dmarc=fail"
    assert _verdict(_list(header)) is EmailAuthenticationVerdict.FAIL


# ── A property qualifies its own clause, not a neighbour's ──────────────────


def test_a_passing_signature_cannot_borrow_the_victims_domain():
    """``header.d`` says who signed *this* signature.

    Collected across the whole header, the failing clause's ``header.d`` was
    still in the map when the passing clause was read, so a ``dkim=pass`` with
    no domain of its own inherited the victim's.
    """
    header = "mx.google.com; dkim=fail header.d=corp.com; dkim=pass"
    assert _verdict(_list(header)) is not EmailAuthenticationVerdict.PASS


def test_a_genuine_second_signature_still_authenticates():
    """Multiple DKIM signatures are ordinary and one failing is not fatal.

    The per-clause read has to keep this working, or the fix above would be a
    regression dressed as a hardening.
    """
    header = "mx.google.com; dkim=fail header.d=other.com; dkim=pass header.d=corp.com"
    assert _verdict(_list(header)) is EmailAuthenticationVerdict.PASS


def test_an_unaligned_spf_pass_authenticates_nothing():
    """SPF passing for the bounce domain says nothing about the From line."""
    header = "mx.google.com; spf=pass smtp.mailfrom=bounce@bulk-mailer.net"
    assert _verdict(_list(header)) is not EmailAuthenticationVerdict.PASS


# ── The shape Resend actually sends ─────────────────────────────────────────


def test_a_repeated_header_in_the_dict_shape_keeps_the_receivers_verdict():
    """``str(["…fail…", "…pass…"])`` collapsed both into one blob.

    Last-wins then handed the forgery the verdict, and the authserv-id parsed
    as ``"['mx.google.com"`` -- so naming a trusted receiver stopped matching
    anything either. This is the production path.
    """
    headers = {
        "authentication-results": [
            "mx.google.com; dmarc=fail header.from=corp.com",
            "evil.example; dmarc=pass header.from=corp.com",
        ]
    }
    assert _verdict(headers) is EmailAuthenticationVerdict.FAIL


def test_a_json_array_inside_a_string_is_expanded_the_same_way():
    """Resend has been observed encoding a repeated header exactly like this."""
    headers = {
        "authentication-results": (
            '["mx.google.com; dmarc=fail header.from=corp.com",'
            ' "evil.example; dmarc=pass header.from=corp.com"]'
        )
    }
    assert _verdict(headers) is EmailAuthenticationVerdict.FAIL


def test_an_ordinary_dict_header_still_passes():
    headers = {
        "authentication-results": "mx.google.com; dmarc=pass header.from=corp.com"
    }
    assert _verdict(headers) is EmailAuthenticationVerdict.PASS


def test_a_dict_header_name_is_matched_whatever_its_case():
    headers = {"Authentication-Results": "mx.google.com; dmarc=pass"}
    assert _verdict(headers) is EmailAuthenticationVerdict.PASS


def test_a_repeated_dict_header_expands_to_separate_values_in_order():
    """Order is the whole defence when no authserv-id is configured."""
    headers = {"authentication-results": ["first; dmarc=pass", "second; dmarc=fail"]}
    assert authentication_results_values(headers) == [
        "first; dmarc=pass",
        "second; dmarc=fail",
    ]


@pytest.mark.parametrize("raw", [None, "", 0, "not-a-header-collection"])
def test_a_shape_we_do_not_recognise_is_no_header_rather_than_a_crash(raw):
    assert authentication_results_values(raw) == []


# ── Results the receiver can state, and what each one means ─────────────────


@pytest.mark.parametrize("result", ["fail", "reject", "quarantine"])
def test_every_way_dmarc_says_no_is_a_failure(result):
    assert (
        _verdict(_list(f"mx.google.com; dmarc={result}"))
        is EmailAuthenticationVerdict.FAIL
    )


@pytest.mark.parametrize("result", ["softfail", "neutral", "none", "temperror"])
def test_an_spf_result_that_is_neither_pass_nor_refusal_is_unknown(result):
    """Only ``fail`` and ``permerror`` are the receiver saying no.

    A ``temperror`` is the receiver failing to ask, which is a deployment
    problem, not a verdict about the sender.
    """
    assert (
        _verdict(_list(f"mx.google.com; spf={result} smtp.mailfrom=x@evil.com"))
        is EmailAuthenticationVerdict.UNKNOWN
    )


def test_an_spf_permerror_is_a_refusal():
    assert (
        _verdict(_list("mx.google.com; spf=permerror"))
        is EmailAuthenticationVerdict.FAIL
    )


def test_results_are_read_whatever_their_case():
    assert (
        _verdict(_list("mx.google.com; DMARC=PASS header.from=corp.com"))
        is EmailAuthenticationVerdict.PASS
    )


def test_the_helo_domain_stands_in_when_there_is_no_envelope_sender():
    assert (
        _verdict(_list("mx.google.com; spf=pass smtp.helo=corp.com"))
        is EmailAuthenticationVerdict.PASS
    )


def test_a_quoted_property_value_is_unwrapped_before_it_is_compared():
    assert (
        _verdict(_list('mx.google.com; dkim=pass header.d="corp.com"'))
        is EmailAuthenticationVerdict.PASS
    )


# ── Who is making the claim ─────────────────────────────────────────────────


def test_an_authserv_id_carrying_a_version_token_still_matches_its_name():
    """RFC 8601 allows a version after the id; ``mx.google.com 1`` is still it."""
    assert (
        _verdict(_list("mx.google.com 1; dmarc=pass"), trusted=RECEIVER)
        is EmailAuthenticationVerdict.PASS
    )


def test_an_authserv_id_wrapped_in_a_comment_still_matches_its_name():
    assert (
        _verdict(_list("mx.google.com (Google) ; dmarc=pass"), trusted=RECEIVER)
        is EmailAuthenticationVerdict.PASS
    )


def test_a_forged_header_is_ignored_when_the_receiver_is_named():
    headers = _list("evil.example; dmarc=pass", "mx.google.com; dmarc=fail")
    assert _verdict(headers, trusted=RECEIVER) is EmailAuthenticationVerdict.FAIL
