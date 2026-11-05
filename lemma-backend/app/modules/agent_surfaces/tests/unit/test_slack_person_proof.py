"""Reading Slack's ID token: only Slack's signature, for Lemma's app, with this nonce."""

from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.modules.agent_surfaces.domain.errors import SlackSignInError
from app.modules.agent_surfaces.services.slack_person_proof import (
    SLACK_OIDC_ISSUER,
    identity_from_id_token,
)

CLIENT = "123.456"


def _key(kid: str = "k1") -> tuple[rsa.RSAPrivateKey, dict]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
    return private, {**public, "kid": kid, "alg": "RS256"}


def _token(private, **claims) -> str:
    now = int(time.time())
    body = {
        "iss": SLACK_OIDC_ISSUER,
        "aud": CLIENT,
        "sub": "U1",
        "iat": now,
        "exp": now + 300,
        "nonce": "n-1",
        "https://slack.com/user_id": "U1",
        "https://slack.com/team_id": "T1",
        **claims,
    }
    return jwt.encode(body, private, algorithm="RS256", headers={"kid": "k1"})


def test_slacks_token_names_the_user_and_workspace():
    private, public = _key()
    identity = identity_from_id_token(
        _token(private), keys={"keys": [public]}, client_id=CLIENT, nonce="n-1"
    )
    assert (identity.team_id, identity.slack_user_id) == ("T1", "U1")


def test_a_token_signed_by_another_key_is_refused():
    private, _ = _key()
    _, stranger = _key()
    with pytest.raises(SlackSignInError):
        identity_from_id_token(
            _token(private), keys={"keys": [stranger]}, client_id=CLIENT, nonce="n-1"
        )


def test_a_token_for_another_app_is_refused():
    private, public = _key()
    with pytest.raises(SlackSignInError):
        identity_from_id_token(
            _token(private, aud="other-app"),
            keys={"keys": [public]},
            client_id=CLIENT,
            nonce="n-1",
        )


def test_a_token_from_another_sign_in_is_refused():
    """The nonce ties the token to the sign-in this browser started."""
    private, public = _key()
    with pytest.raises(SlackSignInError) as refused:
        identity_from_id_token(
            _token(private), keys={"keys": [public]}, client_id=CLIENT, nonce="n-2"
        )
    assert refused.value.reason == "expired"


def test_an_expired_token_is_refused():
    private, public = _key()
    with pytest.raises(SlackSignInError):
        identity_from_id_token(
            _token(private, exp=int(time.time()) - 60, iat=int(time.time()) - 600),
            keys={"keys": [public]},
            client_id=CLIENT,
            nonce="n-1",
        )


def test_a_token_minted_by_a_clock_a_little_ahead_is_accepted():
    """Slack mints the token a moment before Lemma checks it, so a clock a few
    seconds behind Slack's reads its ``iat`` as in the future."""
    private, public = _key()
    ahead = int(time.time()) + 5
    identity = identity_from_id_token(
        _token(private, iat=ahead, exp=ahead + 300),
        keys={"keys": [public]},
        client_id=CLIENT,
        nonce="n-1",
    )
    assert identity.slack_user_id == "U1"


def test_a_token_issued_well_in_the_future_is_still_refused():
    private, public = _key()
    later = int(time.time()) + 600
    with pytest.raises(SlackSignInError):
        identity_from_id_token(
            _token(private, iat=later, exp=later + 300),
            keys={"keys": [public]},
            client_id=CLIENT,
            nonce="n-1",
        )
