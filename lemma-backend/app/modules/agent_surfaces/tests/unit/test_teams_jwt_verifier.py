"""The Bot Framework token on an inbound Teams activity, and what it must bind.

A local server stands in for Microsoft's OpenID metadata and JWKS, so the
verifier fetches, caches and refetches over a real socket; Redis is fakeredis.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import fakeredis
import jwt
import pytest
import pytest_asyncio
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.errors import SurfaceWebhookAuthenticationError
from app.modules.agent_surfaces.services.teams_jwt_verifier import TeamsJwtVerifier

pytestmark = pytest.mark.asyncio

APP_ID = "teams-app-id"
SERVICE_URL = "https://smba.trafficmanager.net/amer/"
ISSUER = "https://api.botframework.com"


@dataclass
class _Signer:
    kid: str
    key: rsa.RSAPrivateKey = field(
        default_factory=lambda: rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
    )
    endorsements: list[str] | None = None

    def jwk(self) -> dict[str, object]:
        public = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        public["kid"] = self.kid
        if self.endorsements is not None:
            public["endorsements"] = self.endorsements
        return public

    def token(self, **overrides: object) -> str:
        now = int(time.time())
        claims: dict[str, object] = {
            "iss": ISSUER,
            "aud": APP_ID,
            "iat": now,
            "nbf": now - 10,
            "exp": now + 600,
            "serviceurl": SERVICE_URL,
        }
        claims.update(overrides)
        claims = {name: value for name, value in claims.items() if value is not None}
        return jwt.encode(
            claims, self.key, algorithm="RS256", headers={"kid": self.kid}
        )


class _BotFrameworkMetadata:
    """OpenID metadata and a JWKS whose keys a test can rotate."""

    def __init__(self) -> None:
        self.verifier: TeamsJwtVerifier | None = None
        self.signers: list[_Signer] = []
        self.jwks_fetches = 0
        self._runner: web.AppRunner | None = None
        self.base_url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/openid", self._openid)
        app.router.add_get("/keys", self._keys)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host="127.0.0.1", port=0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.base_url = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _openid(self, request: web.Request) -> web.Response:
        del request
        return web.json_response({"jwks_uri": f"{self.base_url}/keys"})

    async def _keys(self, request: web.Request) -> web.Response:
        del request
        self.jwks_fetches += 1
        return web.json_response({"keys": [signer.jwk() for signer in self.signers]})


@pytest_asyncio.fixture
async def metadata(monkeypatch) -> AsyncIterator[_BotFrameworkMetadata]:
    server = _BotFrameworkMetadata()
    await server.start()
    monkeypatch.setattr(
        surface_settings,
        "microsoft_bot_openid_config_url",
        f"{server.base_url}/openid",
    )
    cache = RedisJsonCache(
        redis_url=settings.redis_url, key_prefix="surface:oidc", ttl_seconds=600
    )
    cache._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    server.verifier = TeamsJwtVerifier(cache)
    try:
        yield server
    finally:
        await server.stop()


def _activity(service_url: str = SERVICE_URL, channel_id: str = "msteams") -> bytes:
    return json.dumps(
        {"type": "message", "serviceUrl": service_url, "channelId": channel_id}
    ).encode()


async def _verify(
    metadata: _BotFrameworkMetadata, token: str, body: bytes | None = None
) -> None:
    assert metadata.verifier is not None
    await metadata.verifier.verify(
        headers={"Authorization": f"Bearer {token}"},
        raw_body=body if body is not None else _activity(),
        expected_app_id=APP_ID,
    )


async def test_a_token_bound_to_the_activity_is_accepted(metadata) -> None:
    signer = _Signer(kid="k1", endorsements=["msteams"])
    metadata.signers.append(signer)

    await _verify(metadata, signer.token())


async def test_a_token_for_another_bot_is_refused(metadata) -> None:
    signer = _Signer(kid="k1")
    metadata.signers.append(signer)

    with pytest.raises(SurfaceWebhookAuthenticationError) as refused:
        await _verify(metadata, signer.token(aud="someone-elses-bot"))
    assert refused.value.status_code == 401


async def test_a_token_from_a_foreign_issuer_is_refused(metadata) -> None:
    signer = _Signer(kid="k1")
    metadata.signers.append(signer)

    with pytest.raises(SurfaceWebhookAuthenticationError, match="issuer"):
        await _verify(metadata, signer.token(iss="https://login.example.test"))


async def test_an_expired_token_is_refused(metadata) -> None:
    signer = _Signer(kid="k1")
    metadata.signers.append(signer)
    long_ago = int(time.time()) - 3600

    with pytest.raises(SurfaceWebhookAuthenticationError, match="Invalid"):
        await _verify(
            metadata, signer.token(iat=long_ago, nbf=long_ago, exp=long_ago + 600)
        )


async def test_a_token_a_few_minutes_past_expiry_is_within_clock_skew(
    metadata,
) -> None:
    signer = _Signer(kid="k1")
    metadata.signers.append(signer)
    now = int(time.time())

    await _verify(metadata, signer.token(iat=now - 900, nbf=now - 900, exp=now - 120))


async def test_a_token_bound_to_another_service_url_is_refused(metadata) -> None:
    signer = _Signer(kid="k1")
    metadata.signers.append(signer)
    token = signer.token(serviceurl=SERVICE_URL)

    with pytest.raises(SurfaceWebhookAuthenticationError, match="serviceUrl"):
        await _verify(metadata, token, _activity(service_url="https://evil.example/"))


async def test_a_token_without_a_service_url_claim_is_refused(metadata) -> None:
    signer = _Signer(kid="k1")
    metadata.signers.append(signer)

    with pytest.raises(SurfaceWebhookAuthenticationError, match="serviceUrl"):
        await _verify(metadata, signer.token(serviceurl=None))


async def test_service_url_comparison_ignores_trailing_slash_and_host_case(
    metadata,
) -> None:
    signer = _Signer(kid="k1")
    metadata.signers.append(signer)

    await _verify(
        metadata,
        signer.token(serviceurl="https://SMBA.trafficmanager.net/amer"),
        _activity(service_url="https://smba.trafficmanager.net/amer/"),
    )


async def test_a_key_not_endorsed_for_the_channel_is_refused(metadata) -> None:
    signer = _Signer(kid="k1", endorsements=["skype"])
    metadata.signers.append(signer)

    with pytest.raises(SurfaceWebhookAuthenticationError, match="endorsed"):
        await _verify(metadata, signer.token())


async def test_an_unknown_key_id_refetches_the_jwks_once(metadata) -> None:
    old = _Signer(kid="old")
    metadata.signers.append(old)
    await _verify(metadata, old.token())
    assert metadata.jwks_fetches == 1

    rotated = _Signer(kid="rotated")
    metadata.signers.append(rotated)
    await _verify(metadata, rotated.token())

    assert metadata.jwks_fetches == 2


async def test_unknown_key_ids_do_not_refetch_on_every_request(metadata) -> None:
    metadata.signers.append(_Signer(kid="published"))
    stranger = _Signer(kid="never-published")

    for _ in range(3):
        with pytest.raises(SurfaceWebhookAuthenticationError, match="signing key"):
            await _verify(metadata, stranger.token())

    # The first request fills the cache and the second-chance refetch is
    # spent; the rest wait out the cooldown instead of hammering the JWKS.
    assert metadata.jwks_fetches == 2


@pytest.mark.parametrize(
    "header",
    ["", "Basic dXNlcjpwYXNz", "Bearer ", "Bearer not-a-jwt"],
)
async def test_a_missing_or_malformed_bearer_is_refused(metadata, header) -> None:
    metadata.signers.append(_Signer(kid="k1"))

    with pytest.raises(SurfaceWebhookAuthenticationError) as refused:
        assert metadata.verifier is not None
        await metadata.verifier.verify(
            headers={"Authorization": header} if header else {},
            raw_body=_activity(),
            expected_app_id=APP_ID,
        )
    assert refused.value.status_code == 401


async def test_an_hs256_token_is_refused(metadata) -> None:
    metadata.signers.append(_Signer(kid="k1"))
    forged = jwt.encode(
        {"iss": ISSUER, "aud": APP_ID, "exp": int(time.time()) + 600},
        "shared",
        algorithm="HS256",
        headers={"kid": "k1"},
    )

    with pytest.raises(SurfaceWebhookAuthenticationError):
        await _verify(metadata, forged)
