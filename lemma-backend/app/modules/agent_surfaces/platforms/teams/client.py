"""Microsoft Graph / Bot Framework helpers shared by the Teams adapter and tools.

Single home for token acquisition (client_credentials, cached per
tenant+scope), Graph GET helpers, and the raw-team-id → AAD-group-id
resolution that both the inbound adapter and the tool service need.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import aiohttp

from app.modules.agent_surfaces.config import surface_settings
from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger

logger = get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_BOT_SCOPE = "https://api.botframework.com/.default"

# Bot Framework Connector fallback endpoint (when no serviceUrl stored).
# Prefer the serviceUrl from incoming activities — it is region-specific.
BF_FALLBACK_SERVICE_URL = "https://smba.trafficmanager.net/teams/"

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)

# Shared Redis cache of OAuth tokens: "tenant:scope" → token, with the Redis TTL
# set to the token's remaining lifetime — so tokens are reused across replicas, not
# refetched per process. Redis unavailable -> refetch (never fails).
_token_cache: RedisJsonCache | None = None


def _get_token_cache() -> RedisJsonCache:
    global _token_cache
    if _token_cache is None or _token_cache._redis_url != settings.redis_url:
        _token_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surface:teams-token",
            ttl_seconds=3600,
        )
    return _token_cache


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def looks_like_guid(value: str | None) -> bool:
    return bool(value and _GUID_RE.match(str(value)))


def bf_service_url(service_url: str | None) -> str:
    return (
        str(service_url or "").rstrip("/")
        or BF_FALLBACK_SERVICE_URL.rstrip("/")
    )


async def get_graph_token(tenant_id: str) -> str | None:
    # Graph API calls operate on the *customer's* tenant — use their tenant_id.
    return await _get_token(tenant_id, _GRAPH_SCOPE)


async def get_bot_token() -> str | None:
    # Bot Framework tokens must be issued from *Lemma's* bot tenant, not the
    # customer's. Using the customer tenant returns 401 from smba endpoints.
    # Priority: explicit MICROSOFT_BOT_TENANT_ID → "botframework.com" (multi-tenant default).
    bot_tenant = surface_settings.microsoft_bot_tenant_id or "botframework.com"
    return await _get_token(bot_tenant, _BOT_SCOPE)


async def _get_token(tenant_id: str, scope: str) -> str | None:
    app_id = surface_settings.microsoft_bot_app_id
    app_password = surface_settings.microsoft_bot_app_password
    if not app_id or not app_password:
        logger.warning(
            "Teams token acquisition skipped: MICROSOFT_BOT_APP_ID or "
            "MICROSOFT_BOT_APP_PASSWORD not set in environment."
        )
        return None

    cache_key = f"{tenant_id}:{scope}"
    cache = _get_token_cache()
    try:
        cached_token = await cache.get_raw(cache_key)
    except Exception:
        cached_token = None
    if cached_token:
        return cached_token

    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": app_id,
        "client_secret": app_password,
        "scope": scope,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data) as response:
            if response.status >= 400:
                try:
                    err_body = await response.json(content_type=None)
                except Exception:
                    err_body = {}
                error_code = err_body.get("error", "unknown")
                error_desc = str(err_body.get("error_description", ""))[:300]
                if (
                    "AADSTS65001" in error_desc
                    or "AADSTS700016" in error_desc
                    or error_code in ("unauthorized_client", "invalid_client")
                ):
                    logger.error(
                        "Teams token acquisition FAILED — admin consent required. "
                        "Tenant admin for tenant=%s must grant consent at: "
                        "https://login.microsoftonline.com/%s/adminconsent"
                        "?client_id=%s "
                        "status=%s error=%s description=%s",
                        tenant_id,
                        tenant_id,
                        app_id,
                        response.status,
                        error_code,
                        error_desc,
                    )
                else:
                    logger.warning(
                        "Teams token acquisition failed for tenant=%s scope=%s "
                        "status=%s error=%s description=%s",
                        tenant_id,
                        scope,
                        response.status,
                        error_code,
                        error_desc,
                    )
                return None
            result = await response.json()

    token = result.get("access_token")
    if not token:
        logger.warning(
            "Teams token acquisition: no access_token in response for tenant=%s scope=%s",
            tenant_id,
            scope,
        )
        return None

    expires_in = int(result.get("expires_in", 3600))
    # Subtract 60 s so we refresh before the token actually expires.
    try:
        await cache.set_raw(cache_key, str(token), ttl_seconds=max(60, expires_in - 60))
    except Exception:
        pass
    return str(token)


async def get_json(url: str, token: str) -> dict[str, Any] | None:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=auth_headers(token)) as response:
            if response.status >= 400:
                return None
            return await response.json()


async def resolve_graph_team_id(
    *,
    raw_team_id: str | None,
    team_aad_group_id: str | None = None,
    service_url: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> str | None:
    """Resolve a Bot Framework team id (``19:...@thread.tacv2``) to the AAD
    group id that Graph endpoints expect. GUIDs pass through unchanged."""
    if looks_like_guid(team_aad_group_id):
        return str(team_aad_group_id)
    if looks_like_guid(raw_team_id):
        return str(raw_team_id)
    if not raw_team_id:
        return None

    bot_token = await get_bot_token()
    if not bot_token:
        logger.warning(
            "Teams graph team resolution missing bot token raw_team=%s", raw_team_id
        )
        return None

    details_url = (
        f"{bf_service_url(service_url)}/v3/teams/{quote(str(raw_team_id))}"
    )

    owns_session = session is None
    http = session or aiohttp.ClientSession()
    try:
        async with http.get(details_url, headers=auth_headers(bot_token)) as response:
            if response.status >= 400:
                body = await response.text()
                logger.warning(
                    "Teams could not resolve team details raw_team=%s status=%s body=%s",
                    raw_team_id,
                    response.status,
                    body[:300],
                )
                return None
            details = await response.json()
    except Exception as exc:
        logger.warning(
            "Teams team id resolution failed for %s: %s", raw_team_id, exc
        )
        return None
    finally:
        if owns_session:
            await http.close()

    aad_group_id = str(details.get("aadGroupId") or "") or None
    if not aad_group_id:
        logger.warning(
            "Teams team details for raw_team=%s did not include aadGroupId",
            raw_team_id,
        )
        return None
    return aad_group_id
