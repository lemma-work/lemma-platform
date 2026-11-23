"""The phone's native Sign in with Apple, through SuperTokens' own `/signinup`.

Against the real SuperTokens core, the app's routes, its sign-in override and
the database. Only Apple's network is stood in for: its OIDC discovery, the
key its id tokens are checked with (one made here, and checked the way
SuperTokens checks Apple's, audience included), and its token endpoint.
Everything else is the request the phone sends and the calls it makes after.
"""

from __future__ import annotations

import base64
import functools
import json
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import func, select
from supertokens_python.recipe.thirdparty.interfaces import RecipeInterface
from supertokens_python.recipe.thirdparty.providers import config_utils, custom
from supertokens_python.recipe.thirdparty.recipe import ThirdPartyRecipe
from supertokens_python.recipe.thirdparty.recipe_implementation import (
    RecipeImplementation,
)
from supertokens_python.recipe.usermetadata.asyncio import get_user_metadata

from app.core.infrastructure.db.session import async_session_maker
from app.modules.identity.config import IdentitySettings
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.supertokens_auth import apple
from app.modules.identity.infrastructure.supertokens_auth.override_thirdparty import (
    override_thirdparty_functions,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

BUNDLE_ID = "com.example.ios"
SERVICES_ID = "com.example.signin"
APPLE_KEYS = "https://appleid.apple.com/auth/keys"

#: Apple's id-token key, as far as this test's SuperTokens knows.
_SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

#: How the phone talks to SuperTokens: tokens in headers, never cookies.
_HEADER_MODE = {"st-auth-mode": "header", "rid": "thirdparty"}


def _apple_id_token(*, sub: str, email: str, audience: str) -> str:
    """An id token in the shape Apple issues to a native sign-in."""
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "https://appleid.apple.com",
            "aud": audience,
            "sub": sub,
            "email": email,
            # Apple sends it as a string.
            "email_verified": "true",
            "iat": now,
            "exp": now + 600,
        },
        _SIGNING_KEY,
        algorithm="RS256",
    )


async def _verified_with_the_stand_in_key(
    id_token: str, jwks_uri: str, audience: str
) -> dict[str, Any]:
    # SuperTokens' own check, against this key instead of Apple's: the
    # audience it asks for is the client the sign-in named.
    assert jwks_uri == APPLE_KEYS
    return jwt.decode(
        id_token,
        _SIGNING_KEY.public_key(),
        audience=[audience],
        algorithms=["RS256"],
    )


async def _apple_discovery(_endpoint: str) -> dict[str, str]:
    return {
        "authorization_endpoint": "https://appleid.apple.com/auth/authorize",
        "token_endpoint": "https://appleid.apple.com/auth/token",
        "jwks_uri": APPLE_KEYS,
    }


@dataclass
class _TokenEndpoint:
    """Apple's token endpoint: trades any code for `sub`'s refresh token."""

    sub: str
    asked: list[httpx.Request] = field(default_factory=list)

    def answer(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "a.access",
                "refresh_token": "r.native-refresh-token",
                # Read, not verified, by the trade: any key signs it here.
                "id_token": jwt.encode({"sub": self.sub}, "k" * 32, algorithm="HS256"),
            },
        )


def _apple_settings(*, bundle_id: str | None) -> IdentitySettings:
    pem = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )
    return IdentitySettings.model_construct(
        apple_client_id=SERVICES_ID,
        apple_team_id="TEAM123456",
        apple_key_id="KEY1234567",
        apple_private_key=SecretStr(pem),
        apple_app_bundle_id=bundle_id,
    )


def _register_apple(
    monkeypatch: pytest.MonkeyPatch, *, bundle_id: str | None
) -> IdentitySettings:
    config = _apple_settings(bundle_id=bundle_id)
    provider = apple.apple_provider(config)
    assert provider is not None
    # The SDK was initialised once for the process, before Apple was set up;
    # the provider joins the list it reads on every sign-in.
    implementation = ThirdPartyRecipe.get_instance().recipe_implementation
    others = [
        held
        for held in implementation.providers  # type: ignore[attr-defined]
        if held.config.third_party_id != "apple"
    ]
    monkeypatch.setattr(implementation, "providers", [*others, provider])
    monkeypatch.setattr(config_utils, "get_oidc_discovery_info", _apple_discovery)
    monkeypatch.setattr(
        custom,
        "verify_id_token_from_jwks_endpoint_and_get_payload",
        _verified_with_the_stand_in_key,
    )
    return config


@pytest.fixture
def apple_with_phone(monkeypatch: pytest.MonkeyPatch) -> IdentitySettings:
    return _register_apple(monkeypatch, bundle_id=BUNDLE_ID)


@pytest.fixture
def apple_without_phone(monkeypatch: pytest.MonkeyPatch) -> IdentitySettings:
    return _register_apple(monkeypatch, bundle_id=None)


@pytest.fixture
def apple_token_endpoint(
    monkeypatch: pytest.MonkeyPatch, apple_with_phone: IdentitySettings
) -> _TokenEndpoint:
    """The sign-in override as `initialization.py` installs it, over
    SuperTokens' own sign-in, with Apple's token endpoint handed in."""
    endpoint = _TokenEndpoint(sub=f"001234.{uuid4().hex}")
    client = httpx.AsyncClient(transport=httpx.MockTransport(endpoint.answer))
    implementation = ThirdPartyRecipe.get_instance().recipe_implementation
    supertokens_own = SimpleNamespace(
        sign_in_up=functools.partial(RecipeImplementation.sign_in_up, implementation)
    )
    ours = override_thirdparty_functions(
        cast(RecipeInterface, supertokens_own),
        trade_native_code=functools.partial(
            apple.with_native_refresh_token, config=apple_with_phone, http=client
        ),
    )
    monkeypatch.setattr(implementation, "sign_in_up", ours.sign_in_up)
    return endpoint


def _phone_sign_in(
    id_token: str, *, name: dict[str, str] | None = None
) -> dict[str, Any]:
    """The body the phone posts (`signInUpBody`, `lemma-mobile/src/session/apple-link.ts`)."""
    tokens: dict[str, Any] = {
        "id_token": id_token,
        apple.NATIVE_CODE_KEY: f"c.{uuid4().hex}",
    }
    if name is not None:
        tokens["user"] = {"name": name}
    return {"thirdPartyId": "apple", "clientType": "ios", "oAuthTokens": tokens}


def _front_token_expiry(response: httpx.Response) -> int:
    front = json.loads(base64.b64decode(response.headers["front-token"]))
    return int(front["ate"])


async def test_a_phone_signs_up_with_apple_and_holds_a_session_of_its_own(
    test_app: FastAPI,
    async_client: httpx.AsyncClient,
    apple_token_endpoint: _TokenEndpoint,
) -> None:
    del test_app
    sub = apple_token_endpoint.sub
    email = f"{uuid4().hex[:12]}@privaterelay.appleid.com"
    id_token = _apple_id_token(sub=sub, email=email, audience=BUNDLE_ID)

    first = await async_client.post(
        "/st/auth/signinup",
        json=_phone_sign_in(id_token, name={"firstName": "Ada", "lastName": "Byron"}),
        headers=_HEADER_MODE,
    )

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "OK", body
    assert body["createdNewRecipeUser"] is True
    user_id = body["user"]["id"]
    assert body["user"]["emails"] == [email]
    # Header mode: the pair the phone keeps, and no cookie for anyone else.
    access = first.headers["st-access-token"]
    refresh = first.headers["st-refresh-token"]
    assert access and refresh
    assert _front_token_expiry(first) > int(time.time() * 1000)

    # The name Apple sends once is kept on the Lemma user it made.
    async with async_session_maker() as db:
        made = await db.scalar(select(User).where(func.lower(User.email) == email))
    assert made is not None
    assert (made.first_name, made.last_name) == ("Ada", "Byron")
    assert str(made.id) == user_id

    # The code was traded as the bundle id, and what a deletion revokes kept.
    [trade] = apple_token_endpoint.asked
    form = {key: values[0] for key, values in parse_qs(trade.content.decode()).items()}
    assert form["client_id"] == BUNDLE_ID
    kept = (await get_user_metadata(user_id)).metadata[apple.REVOCATION_METADATA_KEY]
    assert kept["client_id"] == BUNDLE_ID
    assert "r.native-refresh-token" not in str(kept)

    # The phone's session works where the phone uses it ...
    me = await async_client.get(
        "/users/me", headers={"Authorization": f"Bearer {access}"}
    )
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email
    # ... and refreshes where the phone refreshes every session it holds.
    renewed = await async_client.post(
        "/auth/cli/refresh", json={"refresh_token": refresh}
    )
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["user_id"] == user_id
    assert renewed.json()["email"] == email

    # Signing in again, with no name this time, is the same account.
    again = await async_client.post(
        "/st/auth/signinup",
        json=_phone_sign_in(_apple_id_token(sub=sub, email=email, audience=BUNDLE_ID)),
        headers=_HEADER_MODE,
    )
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "OK"
    assert again.json()["createdNewRecipeUser"] is False
    assert again.json()["user"]["id"] == user_id


async def test_the_web_keeps_its_own_client_beside_the_phones(
    test_app: FastAPI,
    async_client: httpx.AsyncClient,
    apple_with_phone: IdentitySettings,
) -> None:
    # The web's sign-in names no client; with two registered, SuperTokens
    # would refuse it unless naming none still means the web's.
    del test_app
    response = await async_client.get(
        "/st/auth/authorisationurl",
        params={
            "thirdPartyId": "apple",
            "redirectURIOnProviderDashboard": "http://testserver/st/auth/callback/apple",
        },
    )

    assert response.status_code == 200, response.text
    assert f"client_id={SERVICES_ID}" in response.json()["urlWithQueryParams"]


@pytest.mark.parametrize(
    ("registered", "offered"),
    [("apple_with_phone", True), ("apple_without_phone", False)],
)
async def test_the_phone_offers_apple_only_where_its_client_is_registered(
    test_app: FastAPI,
    async_client: httpx.AsyncClient,
    request: pytest.FixtureRequest,
    registered: str,
    offered: bool,
) -> None:
    # The phone's probe (`offersApple`): the URL for its own client, or a 400.
    del test_app
    request.getfixturevalue(registered)

    response = await async_client.get(
        "/st/auth/authorisationurl",
        params={
            "thirdPartyId": "apple",
            "clientType": "ios",
            "redirectURIOnProviderDashboard": "work.lemma.mobile://apple",
        },
    )

    assert response.status_code == (200 if offered else 400), response.text
