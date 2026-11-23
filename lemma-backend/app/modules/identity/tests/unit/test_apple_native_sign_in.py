"""The phone's native Sign in with Apple: its client, and the code it trades.

Apple issues a native sign-in to the app's bundle id, not the web's Services
ID, so the provider registers a second client for it, and the phone names
that client when it posts the id token. The web names none and must keep
getting its own. The authorization code a phone sends beside the id token is
traded for the refresh token a deletion revokes.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from pydantic import SecretStr
from structlog.testing import capture_logs
from supertokens_python.exceptions import BadInputError
from supertokens_python.recipe.thirdparty.providers.apple import Apple

from app.modules.identity.config import IdentitySettings
from app.modules.identity.infrastructure.supertokens_auth.apple import (
    APPLE_TOKEN_ENDPOINT,
    NATIVE_CLIENT_TYPE,
    NATIVE_CODE_KEY,
    WEB_CLIENT_TYPE,
    apple_provider,
    with_native_refresh_token,
)

pytestmark = pytest.mark.unit

PEM = (
    ec.generate_private_key(ec.SECP256R1())
    .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    .decode()
)
WEB_ONLY = IdentitySettings.model_construct(
    apple_client_id="com.example.signin",
    apple_team_id="TEAM123456",
    apple_key_id="KEY1234567",
    apple_private_key=SecretStr(PEM),
)
WITH_PHONE = WEB_ONLY.model_copy(update={"apple_app_bundle_id": "com.example.ios"})

#: What the phone posts: Apple's id token, verified by SuperTokens before the
#: override runs, and the code beside it.
SUB = "001234.abcdef"
VERIFIED = {"aud": "com.example.ios", "sub": SUB}
SENT = {"id_token": "x", NATIVE_CODE_KEY: "c.one-time-code"}


def _id_token(sub: str) -> str:
    # Apple's answer is read, not verified; any key signs it here.
    return jwt.encode(
        {"sub": sub, "iat": int(time.time())}, "k" * 32, algorithm="HS256"
    )


def _apple(
    answer: httpx.Response | Exception,
) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    asked: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return httpx.AsyncClient(transport=httpx.MockTransport(handle)), asked


def _traded(sub: str = SUB) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": "a.access",
            "refresh_token": "r.refresh-token",
            "id_token": _id_token(sub),
            "token_type": "Bearer",
        },
    )


def _form(request: httpx.Request) -> dict[str, str]:
    return {
        key: values[0] for key, values in parse_qs(request.content.decode()).items()
    }


# -- the provider's clients ---------------------------------------------------


def test_without_a_bundle_id_only_the_web_is_registered():
    provider = apple_provider(WEB_ONLY)

    assert provider is not None
    [web] = provider.config.clients or []
    assert (web.client_type, web.client_id) == (WEB_CLIENT_TYPE, "com.example.signin")


def test_a_bundle_id_adds_the_phone_beside_the_web():
    provider = apple_provider(WITH_PHONE)

    assert provider is not None
    web, phone = provider.config.clients or []
    assert (web.client_type, web.client_id) == (WEB_CLIENT_TYPE, "com.example.signin")
    assert (phone.client_type, phone.client_id) == (
        NATIVE_CLIENT_TYPE,
        "com.example.ios",
    )
    # The name, asked for on the phone too: Apple sends it on the first only.
    assert phone.scope == ["openid", "email", "name"]
    assert phone.additional_config == web.additional_config


def test_a_bundle_id_alone_registers_nothing():
    # Signing the phone's client secret takes the same team, key id and key.
    phone_only = IdentitySettings.model_construct(apple_app_bundle_id="com.example.ios")

    assert apple_provider(phone_only) is None


def test_a_blank_bundle_id_is_no_bundle_id():
    provider = apple_provider(WEB_ONLY.model_copy(update={"apple_app_bundle_id": "  "}))

    assert provider is not None
    assert len(provider.config.clients or []) == 1


async def test_a_call_that_names_no_client_gets_the_webs():
    # The web's sign-in names none, and SuperTokens refuses that outright once
    # a provider has two clients: without this every web sign-in would fail.
    provider = Apple(apple_provider(WITH_PHONE))  # type: ignore[arg-type]

    web = await provider.get_config_for_client_type(None, {})

    assert web.client_id == "com.example.signin"


async def test_the_phone_gets_its_own_client_and_secret():
    provider = Apple(apple_provider(WITH_PHONE))  # type: ignore[arg-type]

    phone = await provider.get_config_for_client_type(NATIVE_CLIENT_TYPE, {})

    assert phone.client_id == "com.example.ios"
    assert phone.client_secret
    claims = jwt.decode(phone.client_secret, options={"verify_signature": False})
    assert claims["sub"] == "com.example.ios"


async def test_a_client_that_is_not_registered_is_a_bad_request():
    # Before the bundle id is set this is what the phone's probe gets: a 400
    # it reads as "not offered", not a 500 in the error log.
    provider = Apple(apple_provider(WEB_ONLY))  # type: ignore[arg-type]

    with pytest.raises(BadInputError):
        await provider.get_config_for_client_type(NATIVE_CLIENT_TYPE, {})


# -- trading the phone's code ---------------------------------------------------


async def test_the_code_is_traded_for_a_refresh_token_as_the_bundle_id():
    apple, asked = _apple(_traded())
    async with apple:
        tokens = await with_native_refresh_token(
            SENT, VERIFIED, config=WITH_PHONE, http=apple
        )

    assert tokens["refresh_token"] == "r.refresh-token"
    assert tokens["id_token"] == "x"
    [request] = asked
    assert str(request.url) == APPLE_TOKEN_ENDPOINT
    form = _form(request)
    assert form["client_id"] == "com.example.ios"
    assert form["code"] == "c.one-time-code"
    assert form["grant_type"] == "authorization_code"
    # A native code is traded without a redirect URI: there was none.
    assert "redirect_uri" not in form
    claims = jwt.decode(form["client_secret"], options={"verify_signature": False})
    assert (claims["iss"], claims["sub"]) == ("TEAM123456", "com.example.ios")


@pytest.mark.parametrize(
    ("tokens", "payload", "config"),
    [
        # The web's exchange already brought one.
        ({"refresh_token": "r.web", NATIVE_CODE_KEY: "c"}, VERIFIED, WITH_PHONE),
        # An older phone that sends only the id token.
        ({"id_token": "x"}, VERIFIED, WITH_PHONE),
        # A code beside an id token issued to someone other than the phone.
        (SENT, {"aud": "com.example.signin", "sub": SUB}, WITH_PHONE),
        # No bundle id: the phone is not registered here at all.
        (SENT, VERIFIED, WEB_ONLY),
    ],
    ids=["already-has-one", "no-code", "not-the-phone", "no-bundle-id"],
)
async def test_nothing_is_asked_of_apple_when_there_is_no_phone_code_to_trade(
    tokens: dict[str, object], payload: dict[str, object], config: IdentitySettings
):
    apple, asked = _apple(_traded())
    async with apple:
        kept = await with_native_refresh_token(
            tokens, payload, config=config, http=apple
        )

    assert kept == tokens
    assert asked == []


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(400, json={"error": "invalid_grant"}),
        httpx.Response(503),
        httpx.Response(200, json={"access_token": "a.access"}),
        httpx.ConnectError("refused"),
    ],
    ids=["refused", "down", "no-refresh-token", "unreachable"],
)
async def test_a_code_apple_does_not_trade_leaves_the_sign_in_as_it_was(
    answer: httpx.Response | Exception,
):
    # The person is signed in either way; only the revocation is lost, and
    # that is logged rather than failing a sign-in Apple already vouched for.
    apple, _ = _apple(answer)
    # Read from structlog itself: `caplog` sees its events only in the first
    # test of a run.
    with capture_logs() as said:
        async with apple:
            tokens = await with_native_refresh_token(
                SENT, VERIFIED, config=WITH_PHONE, http=apple
            )

    assert tokens == SENT
    assert [event["event"] for event in said] == [
        "identity.apple_sign_in.code_not_traded"
    ]
    assert "c.one-time-code" not in str(said)


async def test_a_code_someone_else_signed_in_with_is_not_kept():
    # Deleting this account would otherwise revoke the other person's sign-in.
    apple, _ = _apple(_traded(sub="009999.someone-else"))
    async with apple:
        tokens = await with_native_refresh_token(
            SENT, VERIFIED, config=WITH_PHONE, http=apple
        )

    assert "refresh_token" not in tokens
