"""Verify the Bot Framework JWT that authenticates an inbound Teams activity.

Teams is the one platform whose webhook is authenticated by a signed bearer
token rather than a shared secret, so it needs the OpenID metadata, the JWKS
and the claim rules Microsoft publishes for Bot Framework channels. Kept apart
from the shared-secret schemes in ``webhook_security_service`` because none of
that machinery is shared with them.

A signature, audience and issuer are not enough on their own. The token is
valid for about an hour and says nothing about the body it arrived with, so a
captured token could carry any activity -- including a ``serviceUrl`` naming a
host of the sender's choosing, to which every reply would then post Lemma's
bot token. Microsoft's rules bind the token to the activity through the
``serviceurl`` claim and to the channel through the signing key's
endorsements; both are checked here.
"""

from __future__ import annotations

from app.modules.agent_surfaces.platforms.outbound_http import outbound_http

import json
from dataclasses import dataclass
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import RSAAlgorithm
from redis.exceptions import RedisError

from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.caches import TEAMS_OIDC, surface_caches
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.errors import SurfaceWebhookAuthenticationError
from app.modules.agent_surfaces.platforms.teams.attachment_urls import (
    normalized_service_url,
)

logger = get_logger(__name__)

_BOT_FRAMEWORK_OPENID_CONFIG_URL = (
    "https://login.botframework.com/v1/.well-known/openidconfiguration"
)
_BOT_FRAMEWORK_ALLOWED_ISSUERS = frozenset(
    {
        "https://api.botframework.com",
        "https://api.botframework.com/",
    }
)
_OIDC_CACHE_TTL_SECONDS = TEAMS_OIDC.ttl_seconds
# Microsoft's guidance for Bot Framework token validation: clocks on either side
# drift, and five minutes is the skew it tells bots to tolerate.
_CLOCK_SKEW_LEEWAY_SECONDS = 300
# Microsoft rotates signing keys, and a key we have not seen is most often a
# new one. A bounded refetch picks it up without waiting out the JWKS cache,
# but an unknown ``kid`` is also something anyone can send, so the refetch is
# rate limited across replicas rather than done per request.
_JWKS_REFETCH_COOLDOWN_SECONDS = 60


@dataclass(frozen=True, slots=True)
class TeamsActivityBinding:
    """The two fields of an activity body that its token must vouch for."""

    service_url: str
    channel_id: str

    @classmethod
    def from_raw_body(cls, raw_body: bytes) -> TeamsActivityBinding:
        try:
            # A provider's JSON: no shape until the two fields are read out.
            payload = json.loads(raw_body or b"{}")
        except ValueError as exc:
            raise SurfaceWebhookAuthenticationError(
                "Teams activity body is not JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise SurfaceWebhookAuthenticationError(
                "Teams activity body is not an object"
            )
        return cls(
            service_url=str(payload.get("serviceUrl") or ""),
            channel_id=str(payload.get("channelId") or ""),
        )


def _get_oidc_cache() -> RedisJsonCache:
    """OIDC metadata and JWKS for Teams tokens, fetched once across replicas.
    Redis down -> refetch."""
    return surface_caches.get(TEAMS_OIDC)


def _bearer_token(headers: dict[str, str]) -> str:
    auth_header = headers.get("authorization") or headers.get("Authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise SurfaceWebhookAuthenticationError("Missing Teams bearer token")
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise SurfaceWebhookAuthenticationError("Missing Teams bearer token")
    return token


class TeamsJwtVerifier:
    """Checks one inbound Teams activity's bearer token.

    ``cache`` holds the OpenID metadata and JWKS; ``None`` uses the shared Redis
    cache every replica reads.
    """

    def __init__(self, cache: RedisJsonCache | None = None) -> None:
        self._injected_cache = cache

    def _cache(self) -> RedisJsonCache:
        return self._injected_cache or _get_oidc_cache()

    async def verify(
        self,
        *,
        headers: dict[str, str],
        raw_body: bytes,
        expected_app_id: str | None,
    ) -> None:
        if not expected_app_id:
            raise SurfaceWebhookAuthenticationError(
                "Teams bot app ID is not configured",
                status_code=503,
            )
        token = _bearer_token(headers)
        activity = TeamsActivityBinding.from_raw_body(raw_body)

        jwks_uri = await self._jwks_uri()
        signing_jwk = await self._signing_jwk(token, jwks_uri)
        public_key = RSAAlgorithm.from_jwk(signing_jwk)
        if not isinstance(public_key, RSAPublicKey):
            raise SurfaceWebhookAuthenticationError(
                "Teams signing key is not a public key"
            )
        try:
            claims = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=expected_app_id,
                leeway=_CLOCK_SKEW_LEEWAY_SECONDS,
                options={"verify_iss": False, "require": ["exp", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise SurfaceWebhookAuthenticationError(
                "Invalid Teams bearer token"
            ) from exc

        issuer = str(claims.get("iss") or "").strip()
        if issuer not in _BOT_FRAMEWORK_ALLOWED_ISSUERS:
            raise SurfaceWebhookAuthenticationError("Invalid Teams token issuer")
        _require_service_url_binding(claims, activity)
        _require_channel_endorsement(signing_jwk, activity)

    async def _jwks_uri(self) -> str:
        openid_url = (
            surface_settings.microsoft_bot_openid_config_url
            or _BOT_FRAMEWORK_OPENID_CONFIG_URL
        )
        openid_config = await self._get_json_cached(openid_url)
        jwks_uri = str(openid_config.get("jwks_uri") or "").strip()
        if not jwks_uri:
            raise SurfaceWebhookAuthenticationError(
                "Teams OpenID metadata is missing jwks_uri",
                status_code=503,
            )
        return jwks_uri

    async def _signing_jwk(self, token: str, jwks_uri: str) -> dict[str, object]:
        key_id = _token_key_id(token)
        jwks = await self._get_json_cached(jwks_uri)
        found = _find_jwk(key_id, jwks.get("keys"))
        if found is None and await self._may_refetch_jwks(jwks_uri):
            jwks = await self._get_json_cached(jwks_uri, refresh=True)
            found = _find_jwk(key_id, jwks.get("keys"))
        if found is None:
            raise SurfaceWebhookAuthenticationError(
                "Unable to resolve Teams signing key"
            )
        return found

    async def _may_refetch_jwks(self, jwks_uri: str) -> bool:
        try:
            return await self._cache().set_raw_if_absent(
                f"refetch:{jwks_uri}", "1", ttl_seconds=_JWKS_REFETCH_COOLDOWN_SECONDS
            )
        except RedisError, OSError, TimeoutError:
            # Without the shared cooldown there is no bound on refetches, so a
            # Redis outage fails closed on unknown keys exactly as before.
            logger.warning(
                "agent_surfaces.teams_jwt_verifier.jwks_refetch_gate_unavailable.degraded",
                exc_info=True,
            )
            return False

    async def _get_json_cached(
        self, url: str, *, refresh: bool = False
    ) -> dict[str, object]:
        cache = self._cache()
        cached = None
        if not refresh:
            try:
                cached = await cache.get_json(url)
            except RedisError, OSError, TimeoutError, ValueError:
                cached = None
        if cached is not None:
            return cached

        async with outbound_http(10) as client:
            response = await client.get(url)
            if response.status_code != 200:
                raise SurfaceWebhookAuthenticationError(
                    f"Failed to load Teams verification metadata from {url}",
                    status_code=503,
                )
            payload = response.json()
        if not isinstance(payload, dict):
            raise SurfaceWebhookAuthenticationError(
                f"Invalid Teams verification metadata from {url}",
                status_code=503,
            )
        try:
            await cache.set_json(url, payload, ttl_seconds=_OIDC_CACHE_TTL_SECONDS)
        except RedisError, OSError, TimeoutError, TypeError:
            # The OIDC metadata was fetched and verified above; caching it only
            # spares the next webhook the same fetch. An uncacheable document is
            # still a valid one, and `payload` is returned below regardless.
            pass
        return payload


def _require_service_url_binding(
    claims: dict[str, object], activity: TeamsActivityBinding
) -> None:
    claimed = normalized_service_url(str(claims.get("serviceurl") or ""))
    if not claimed or claimed != normalized_service_url(activity.service_url):
        raise SurfaceWebhookAuthenticationError(
            "Teams token serviceUrl does not match the activity"
        )


def _require_channel_endorsement(
    signing_jwk: dict[str, object], activity: TeamsActivityBinding
) -> None:
    # Bot Framework keys list the channels they may sign for. A key without the
    # list predates endorsements (and is what test doubles serve); a key with
    # one must name the channel the activity claims to come from.
    endorsements = signing_jwk.get("endorsements")
    if endorsements is None:
        return
    if not isinstance(endorsements, list) or activity.channel_id not in endorsements:
        raise SurfaceWebhookAuthenticationError(
            "Teams signing key is not endorsed for this channel"
        )


def _token_key_id(token: str) -> str:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise SurfaceWebhookAuthenticationError("Malformed Teams bearer token") from exc
    key_id = header.get("kid") or header.get("x5t")
    if not key_id:
        raise SurfaceWebhookAuthenticationError("Teams bearer token is missing key id")
    return str(key_id)


def _find_jwk(key_id: str, keys: object) -> dict[str, object] | None:
    for key in keys if isinstance(keys, list) else []:
        if isinstance(key, dict) and key_id in (key.get("kid"), key.get("x5t")):
            return key
    return None
