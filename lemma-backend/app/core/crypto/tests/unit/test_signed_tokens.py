"""Signed claim tokens: round trip, purpose binding, expiry, tampering."""

import pytest

from app.core.crypto.tokens import (
    InvalidSignedToken,
    mint_signed_token,
    verify_signed_token,
)


def test_a_token_carries_its_claims_and_expiry():
    token = mint_signed_token("visitor-access", {"sid": "s1"}, ttl_seconds=60, now=1000)

    claims = verify_signed_token("visitor-access", token, now=1030)

    assert claims == {"sid": "s1", "exp": 1060}


def test_a_token_for_one_purpose_never_verifies_as_another():
    token = mint_signed_token("visitor-access", {"sid": "s1"}, ttl_seconds=60)

    with pytest.raises(InvalidSignedToken):
        verify_signed_token("function-run", token)


def test_an_expired_token_is_refused():
    token = mint_signed_token("visitor-access", {}, ttl_seconds=60, now=1000)

    with pytest.raises(InvalidSignedToken):
        verify_signed_token("visitor-access", token, now=1061)


def test_a_changed_payload_is_refused():
    token = mint_signed_token("visitor-access", {"cid": "a"}, ttl_seconds=60)
    payload, signature = token.split(".", 1)
    mutated = payload[:-1] + ("A" if payload[-1] != "A" else "B")

    with pytest.raises(InvalidSignedToken):
        verify_signed_token("visitor-access", f"{mutated}.{signature}")


@pytest.mark.parametrize("token", ["", "nodot", "a.b.c", "!!!.k.s"])
def test_garbage_is_refused_not_raised(token):
    with pytest.raises(InvalidSignedToken):
        verify_signed_token("visitor-access", token)


def test_the_caller_cannot_choose_the_expiry():
    with pytest.raises(ValueError):
        mint_signed_token("visitor-access", {"exp": 1}, ttl_seconds=60)
