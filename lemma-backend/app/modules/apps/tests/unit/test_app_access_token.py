"""App access tokens are bound to their purpose, their origin and their expiry."""

from dataclasses import replace
from uuid import uuid4

import pytest

from app.modules.apps.services.app_access import (
    AppAccessClaims,
    AppAccessPurpose,
    mint_app_access_token,
    verify_app_access_token,
)

pytestmark = pytest.mark.unit

ORIGIN = "https://desk.apps.example.test"
NOW = 1_800_000_000


def _claims(**changes) -> AppAccessClaims:
    claims = AppAccessClaims(
        user_id=uuid4(),
        app_id=uuid4(),
        origin=ORIGIN,
        session_handle="handle-1",
        expires_at=NOW + 60,
    )
    return replace(claims, **changes)


def _verify(token, *, purpose=AppAccessPurpose.TICKET, origin=ORIGIN, now=NOW):
    return verify_app_access_token(token, purpose=purpose, origin=origin, now=now)


def test_round_trip_returns_the_signed_claims():
    claims = _claims()
    token = mint_app_access_token(AppAccessPurpose.TICKET, claims)

    assert _verify(token) == claims


@pytest.mark.parametrize("purpose", list(AppAccessPurpose))
def test_a_token_is_refused_for_the_other_purpose(purpose):
    other = next(p for p in AppAccessPurpose if p is not purpose)
    token = mint_app_access_token(purpose, _claims())

    assert _verify(token, purpose=other) is None


def test_a_token_is_refused_on_another_origin():
    token = mint_app_access_token(AppAccessPurpose.TICKET, _claims())

    assert _verify(token, origin="https://other.apps.example.test") is None
    assert _verify(token, origin="https://desk--r2.apps.example.test") is None


def test_an_expired_token_is_refused():
    token = mint_app_access_token(AppAccessPurpose.TICKET, _claims())

    assert _verify(token, now=NOW + 59) is not None
    assert _verify(token, now=NOW + 60) is None


def test_a_changed_payload_is_refused():
    token = mint_app_access_token(AppAccessPurpose.TICKET, _claims())
    forged = mint_app_access_token(AppAccessPurpose.TICKET, _claims(app_id=uuid4()))
    # The forged payload with the original signature.
    spliced = forged.split(".", 1)[0] + "." + token.split(".", 1)[1]

    assert _verify(spliced) is None


@pytest.mark.parametrize(
    "token", [None, "", "no-dots", "!!!.k.s", "e30.kid.sig", "W10.kid.sig"]
)
def test_malformed_tokens_are_refused(token):
    assert _verify(token) is None
