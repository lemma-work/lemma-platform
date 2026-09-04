"""Acting as the GitHub App rather than as the person who connected it.

A GitHub App has two ways to speak. It can act as *itself* against one
installation, with a token minted from its private key and valid an hour; or it
can act as a *person*, with the ordinary user-to-server token the OAuth half of
an install hands back. Lemma uses both deliberately -- an agent's operations run
as the App, so a schedule keeps working after its author leaves and a
webhook-triggered run has an identity with nobody present, while the sandbox's
`git`/`gh` and pod publishing run as the person, because work in someone's
checkout should carry their name.

This module is the first half. Minting is two hops: a short JWT signed with the
App's key proves we are the App, and GitHub trades it for an installation token.

Nothing here reads the database, which is the point. Minting happens during the
execute phase of an operation, where no pooled connection is held -- the resolve
phase only reads which installation to mint for. Putting the HTTP call in the
resolve phase instead would hold a connection across a call to GitHub, which is
the thing the resolve/execute split exists to prevent.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import httpx
import jwt

from app.core.config import settings
from app.core.crypto.factory import get_secret_cipher
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.modules.connectors.config import connector_settings

logger = get_logger(__name__)

_GITHUB_API = "https://api.github.com"
# GitHub refuses a JWT whose lifetime exceeds ten minutes. Nine leaves room for
# clock skew between here and GitHub without ever presenting an expired one.
_JWT_TTL_SECONDS = 9 * 60
# A minted token lasts an hour. Dropping it from the cache early means a call
# never starts with a token that expires mid-flight.
_TOKEN_EXPIRY_SKEW_SECONDS = 5 * 60
_MINT_TIMEOUT_SECONDS = 15.0


class GitHubAppUnavailable(Exception):
    """The deployment cannot act as the App.

    Not an error in the sense of something broken: an install with no private
    key configured is a perfectly good user-token install, and the caller falls
    back to that rather than failing the operation.
    """


@dataclass(frozen=True, slots=True)
class InstallationToken:
    token: str
    expires_at: datetime


@lru_cache(maxsize=1)
def _signing_key() -> str:
    """The App's PEM, read once.

    An `lru_cache` singleton rather than a Redis entry: this is a loaded
    credential handle, which is the sanctioned exception to caching in Redis,
    and the alternative is re-reading a file on every mint.

    Accepts the key inline or as a path, because GitHub hands you a `.pem` and
    a deployment may prefer either. An inline key commonly arrives with escaped
    newlines from an env file, so those are restored.
    """
    inline = connector_settings.connector_github_app_private_key
    if inline is not None:
        return inline.get_secret_value().replace("\\n", "\n")
    path = connector_settings.connector_github_app_private_key_path
    if path:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    raise GitHubAppUnavailable(
        "No GitHub App private key is configured, so Lemma cannot act as the "
        "App. Set CONNECTOR_GITHUB_APP_PRIVATE_KEY or "
        "CONNECTOR_GITHUB_APP_PRIVATE_KEY_PATH."
    )


def app_jwt() -> str:
    """A short-lived assertion that we are the App.

    Issued by the App's client id. GitHub accepts either the numeric App id or
    the client id as `iss`, and the client id is already required for the OAuth
    half -- so there is no second identifier to configure and keep in step.
    """
    # Read from the environment rather than a settings field, because that is
    # where the OAuth half already reads it from -- `env_system_oauth_config`
    # resolves the same `CONNECTOR_GITHUB_CLIENT_ID`, and a second source of
    # truth for one credential is how the two drift apart.
    client_id = _client_id()
    if not client_id:
        raise GitHubAppUnavailable(
            "CONNECTOR_GITHUB_CLIENT_ID is required to identify the App."
        )
    now = int(time.time())
    return jwt.encode(
        # `iat` backdated by a minute: GitHub rejects a token issued in its
        # future, and a clock a few seconds fast is the common way to hit that.
        {"iat": now - 60, "exp": now + _JWT_TTL_SECONDS, "iss": client_id},
        _signing_key(),
        algorithm="RS256",
    )


def _client_id() -> str | None:
    return os.getenv("CONNECTOR_GITHUB_CLIENT_ID") or os.getenv("GITHUB_CLIENT_ID")


def _token_cache() -> RedisJsonCache:
    return RedisJsonCache(
        redis_url=settings.redis_url,
        key_prefix="connectors:github:installation-token",
        ttl_seconds=3600 - _TOKEN_EXPIRY_SKEW_SECONDS,
    )


async def installation_token(
    installation_id: str, *, client: httpx.AsyncClient | None = None
) -> str:
    """A token that acts as the App on one installation.

    Cached in Redis, encrypted, keyed by installation -- every member of an
    organization shares one installation, so a per-account cache would mint the
    same token repeatedly and burn the App's rate budget to do it.
    """
    cache = _token_cache()
    cached = await cache.get_raw(installation_id)
    if cached:
        plaintext = get_secret_cipher().decrypt_str(cached)
        if plaintext:
            return plaintext

    minted = await _mint(installation_id, client=client)
    encrypted = get_secret_cipher().encrypt_str(minted.token)
    if encrypted:
        remaining = int(
            (minted.expires_at - datetime.now(timezone.utc)).total_seconds()
            - _TOKEN_EXPIRY_SKEW_SECONDS
        )
        if remaining > 0:
            await cache.set_raw(installation_id, encrypted, ttl_seconds=remaining)
    return minted.token


async def _mint(
    installation_id: str, *, client: httpx.AsyncClient | None = None
) -> InstallationToken:
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_MINT_TIMEOUT_SECONDS)
    try:
        response = await client.post(
            f"{_GITHUB_API}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 201:
        # The installation is the tenant's, so this is upstream's answer rather
        # than our fault -- most often the App was uninstalled and the id we
        # stored no longer names anything.
        logger.warning(
            "connectors.github_app.installation_token_refused",
            upstream_status=response.status_code,
        )
        raise GitHubAppUnavailable(
            f"GitHub refused an installation token with HTTP {response.status_code}."
        )

    payload = response.json()
    token = payload.get("token")
    if not token:
        raise GitHubAppUnavailable("GitHub returned no installation token.")
    return InstallationToken(
        token=str(token),
        expires_at=_parse_expiry(payload.get("expires_at")),
    )


def _parse_expiry(value: object) -> datetime:
    """When the minted token dies, in UTC.

    Falling back to an hour rather than raising: GitHub documents the lifetime,
    and a missing or unparseable field is a reason to cache conservatively, not
    to fail an operation that has a perfectly good token in hand.
    """
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            return (
                parsed
                if parsed.tzinfo is not None
                else parsed.replace(tzinfo=timezone.utc)
            )
    # GitHub documents an hour. A missing or unparseable field is a reason to
    # assume the documented lifetime, not to treat the token as already dead --
    # which would compute a negative TTL and defeat the cache entirely.
    return datetime.now(timezone.utc) + timedelta(hours=1)
