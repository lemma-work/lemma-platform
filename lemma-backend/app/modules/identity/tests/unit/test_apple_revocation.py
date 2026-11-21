"""Revoking a Sign in with Apple: the request Apple gets, and each answer."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
)
from pydantic import SecretStr

from app.core.infrastructure.work.types import Unavailable, UnavailableEvidence
from app.modules.identity.config import IdentitySettings
from app.modules.identity.infrastructure.supertokens_auth.apple import (
    APPLE_REVOKE_ENDPOINT,
    Revocation,
    apple_client_secret,
    remember_apple_refresh_token,
    revoke_refresh_token,
)

pytestmark = pytest.mark.unit

PEM = (
    ec.generate_private_key(ec.SECP256R1())
    .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    .decode()
)
CONFIGURED = IdentitySettings.model_construct(
    apple_client_id="com.example.signin",
    apple_team_id="TEAM123456",
    apple_key_id="KEY1234567",
    apple_private_key=SecretStr(PEM),
)


def _apple(answer: httpx.Response, asked: list[httpx.Request]) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return answer

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


async def _revoke(answer: httpx.Response) -> tuple[Revocation, list[httpx.Request]]:
    asked: list[httpx.Request] = []
    async with _apple(answer, asked) as http:
        outcome = await revoke_refresh_token(
            CONFIGURED,
            client_id="com.example.signin",
            refresh_token="r.refresh-token",
            http=http,
        )
    return outcome, asked


async def test_revoking_posts_the_refresh_token_with_a_signed_client_secret():
    outcome, asked = await _revoke(httpx.Response(200))

    assert outcome is Revocation.REVOKED
    [request] = asked
    assert str(request.url) == APPLE_REVOKE_ENDPOINT
    form = {
        key: values[0] for key, values in parse_qs(request.content.decode()).items()
    }
    assert form["client_id"] == "com.example.signin"
    assert form["token"] == "r.refresh-token"
    assert form["token_type_hint"] == "refresh_token"
    public_key = load_pem_private_key(PEM.encode(), password=None).public_key()
    claims = jwt.decode(
        form["client_secret"],
        public_key,  # type: ignore[arg-type]  # an EC public key; jwt's union is wider
        algorithms=["ES256"],
        audience="https://appleid.apple.com",
    )
    assert (claims["iss"], claims["sub"]) == ("TEAM123456", "com.example.signin")
    assert jwt.get_unverified_header(form["client_secret"])["kid"] == "KEY1234567"


def test_the_client_secret_is_signed_for_the_client_the_token_was_issued_to():
    # A phone's native sign-in is issued to the bundle id, not the Services ID.
    secret = apple_client_secret(CONFIGURED, "com.example.ios")

    assert secret is not None
    claims = jwt.decode(secret, options={"verify_signature": False})
    assert claims["sub"] == "com.example.ios"
    assert claims["exp"] - claims["iat"] == 300


async def test_a_token_apple_no_longer_honours_is_already_spent():
    outcome, _ = await _revoke(httpx.Response(400, json={"error": "invalid_grant"}))

    assert outcome is Revocation.ALREADY_SPENT


async def test_a_request_apple_refuses_is_final_rather_than_retried():
    outcome, _ = await _revoke(httpx.Response(400, json={"error": "invalid_client"}))

    assert outcome is Revocation.REFUSED


@pytest.mark.parametrize("status", [502, 503, 504])
async def test_apple_being_down_waits_for_it_without_spending_an_attempt(status: int):
    with pytest.raises(Unavailable) as raised:
        await _revoke(httpx.Response(status))

    assert raised.value.evidence is UnavailableEvidence.HEALTH_CHECK_FAILING


async def test_a_rate_limit_waits_as_long_as_apple_asks():
    with pytest.raises(Unavailable) as raised:
        await _revoke(httpx.Response(429, headers={"retry-after": "120"}))

    assert raised.value.evidence is UnavailableEvidence.RATE_LIMITED
    assert raised.value.retry_after == timedelta(seconds=120)


async def test_an_unreachable_apple_waits_for_it():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as http:
        with pytest.raises(Unavailable) as raised:
            await revoke_refresh_token(
                CONFIGURED,
                client_id="com.example.signin",
                refresh_token="r.refresh-token",
                http=http,
            )

    assert raised.value.evidence is UnavailableEvidence.CONNECTION_REFUSED


async def test_without_the_key_nothing_is_asked_of_apple():
    asked: list[httpx.Request] = []
    unconfigured = IdentitySettings.model_construct(apple_team_id="TEAM123456")

    async with _apple(httpx.Response(200), asked) as http:
        outcome = await revoke_refresh_token(
            unconfigured,
            client_id="com.example.signin",
            refresh_token="r.refresh-token",
            http=http,
        )

    assert outcome is Revocation.NOT_CONFIGURED
    assert asked == []


async def test_a_sign_in_that_brought_no_refresh_token_keeps_nothing():
    # A native sign-in that sent only the id token, or a token answer without
    # an audience: there is nothing a later revocation could use.
    assert not await remember_apple_refresh_token(
        "user-id", {"id_token": "x"}, {"aud": "com.example.signin"}
    )
    assert not await remember_apple_refresh_token(
        "user-id", {"refresh_token": "r.refresh-token"}, {}
    )
