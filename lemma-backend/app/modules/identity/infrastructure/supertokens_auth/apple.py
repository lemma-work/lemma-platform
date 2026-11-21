"""Sign in with Apple: the provider, its client secret, and revoking it.

Apple asks an app that offers Sign in with Apple to revoke the person's
authorization when they delete their account (its technote TN3194). Revoking
spends a token Apple issued, and the refresh token from the code exchange is
the only one that outlives the sign-in. So it is kept, sealed with the secret
cipher, in the SuperTokens user's own metadata -- which needs no table of ours
and goes when that user does -- and the deletion's work item spends it just
before it deletes the user.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from contextlib import AsyncExitStack
from datetime import timedelta
from enum import StrEnum
from typing import NamedTuple

import httpx
import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from supertokens_python.recipe.thirdparty.provider import (
    ProviderClientConfig,
    ProviderConfig,
    ProviderInput,
)
from supertokens_python.recipe.usermetadata.asyncio import (
    get_user_metadata,
    update_user_metadata,
)

from app.core.config import reveal_secret
from app.core.crypto import get_secret_cipher
from app.core.infrastructure.work.types import Unavailable, UnavailableEvidence
from app.core.log.log import get_logger
from app.modules.identity.config import IdentitySettings, identity_settings

logger = get_logger(__name__)

# Apple's published endpoints, not choices a deployment makes -- constants for
# the reason Telegram's are (`identity/config.py`).
APPLE_AUDIENCE = "https://appleid.apple.com"
APPLE_REVOKE_ENDPOINT = "https://appleid.apple.com/auth/revoke"

#: The SuperTokens user-metadata key holding what a revocation needs.
REVOCATION_METADATA_KEY = "apple_revocation"

#: Answers that mean Apple is down for everyone rather than refusing this
#: token: the same three the embedding provider is given a free retry for.
_DOWN_STATUSES = frozenset({502, 503, 504})

#: Apple's own limit is six months; a secret minted for one call needs minutes.
_CLIENT_SECRET_TTL_SECONDS = 300


class _Kept(NamedTuple):
    client_id: str
    refresh_token: str


class Revocation(StrEnum):
    REVOKED = "revoked"
    #: Apple no longer honours the token (`invalid_grant`): nothing is left.
    ALREADY_SPENT = "already_spent"
    #: Apple refused the request itself, most likely the client secret.
    REFUSED = "refused"
    #: Team, key id or key are no longer set, so no secret can be signed.
    NOT_CONFIGURED = "not_configured"


def apple_private_key(config: IdentitySettings) -> str | None:
    """The `.p8` key as PEM text, or None when it is not a usable key.

    Read here rather than left to SuperTokens, which loads it only when it
    signs the client secret -- on the click, as a 500. A key that did not
    survive the trip into the environment (a file path where the contents
    belong, a truncated paste) then hides the button and says why in the log.
    """
    raw = reveal_secret(config.apple_private_key) or ""
    pem = raw.strip().strip("\"'").replace("\\n", "\n")
    try:
        key = load_pem_private_key(pem.encode(), password=None)
    except ValueError, TypeError, UnsupportedAlgorithm:
        key = None
    if not isinstance(key, EllipticCurvePrivateKey):
        logger.warning(
            "identity.apple_sign_in.private_key_unusable",
            reason="APPLE_PRIVATE_KEY is not the PEM text of an EC private key",
        )
        return None
    return pem


def apple_provider(config: IdentitySettings) -> ProviderInput | None:
    """Sign in with Apple, or None until all four values are set and usable."""
    if not config.is_apple_oauth_configured():
        return None
    private_key = apple_private_key(config)
    if private_key is None:
        return None
    assert config.apple_client_id is not None
    return ProviderInput(
        config=ProviderConfig(
            # SuperTokens picks its Apple implementation by this id: OIDC
            # discovery, `response_mode=form_post`, and the client secret
            # signed from the three values below.
            third_party_id="apple",
            clients=[
                ProviderClientConfig(
                    client_id=config.apple_client_id,
                    # SuperTokens asks for openid and email only. Apple sends
                    # the name when asked, and only on the first sign-in, so
                    # asking later cannot recover it.
                    scope=["openid", "email", "name"],
                    additional_config={
                        "teamId": config.apple_team_id,
                        "keyId": config.apple_key_id,
                        "privateKey": private_key,
                    },
                ),
            ],
        ),
    )


def apple_client_secret(config: IdentitySettings, client_id: str) -> str | None:
    """The ES256 JWT Apple takes as a client secret, for ``client_id``.

    The shape SuperTokens signs for the code exchange, minted here because a
    revocation happens long after any SuperTokens call would have made one.
    """
    if not (
        config.apple_team_id
        and config.apple_key_id
        and reveal_secret(config.apple_private_key)
    ):
        return None
    private_key = apple_private_key(config)
    if private_key is None:
        return None
    now = int(time.time())
    return jwt.encode(
        {
            "iss": config.apple_team_id,
            "iat": now,
            "exp": now + _CLIENT_SECRET_TTL_SECONDS,
            "aud": APPLE_AUDIENCE,
            "sub": client_id,
        },
        private_key,
        algorithm="ES256",
        headers={"kid": config.apple_key_id},
    )


async def remember_apple_refresh_token(
    user_id: str,
    oauth_tokens: Mapping[str, object],
    id_token_payload: Mapping[str, object] | None,
) -> bool:
    """Keep what revoking this sign-in will need; False when Apple gave none.

    The client id comes from the verified id token's ``aud`` rather than from
    settings: a refresh token can only be revoked by the client it was issued
    to, and a phone's native sign-in is issued to the app's bundle id.
    """
    refresh_token = oauth_tokens.get("refresh_token")
    client_id = (id_token_payload or {}).get("aud")
    if not (isinstance(refresh_token, str) and refresh_token):
        return False
    if not (isinstance(client_id, str) and client_id):
        return False
    sealed = await get_secret_cipher().encrypt_json_async(
        {"refresh_token": refresh_token}
    )
    await update_user_metadata(
        user_id,
        {REVOCATION_METADATA_KEY: {"client_id": client_id, "sealed": sealed}},
    )
    return True


async def revoke_refresh_token(
    config: IdentitySettings,
    *,
    client_id: str,
    refresh_token: str,
    http: httpx.AsyncClient,
) -> Revocation:
    """Ask Apple to revoke ``refresh_token``, and with it the authorization.

    Apple being down raises :class:`Unavailable`, so a work item waits for it
    without spending an attempt. Every other answer is final and returned:
    asking again would be refused the same way.
    """
    client_secret = apple_client_secret(config, client_id)
    if client_secret is None:
        logger.warning(
            "identity.apple_sign_in.revoke_skipped",
            reason="APPLE_TEAM_ID, APPLE_KEY_ID or APPLE_PRIVATE_KEY is not usable",
        )
        return Revocation.NOT_CONFIGURED
    try:
        answer = await http.post(
            APPLE_REVOKE_ENDPOINT,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "token": refresh_token,
                "token_type_hint": "refresh_token",
            },
        )
    except httpx.TransportError as unreachable:
        # The message is ours: it is stored on the work item.
        raise Unavailable(
            f"Apple's revoke endpoint unreachable: {type(unreachable).__name__}",
            evidence=UnavailableEvidence.CONNECTION_REFUSED,
        ) from unreachable
    if answer.status_code == 429:
        raise Unavailable(
            "Apple's revoke endpoint is rate limiting",
            evidence=UnavailableEvidence.RATE_LIMITED,
            retry_after=_retry_after(answer),
        )
    if answer.status_code in _DOWN_STATUSES:
        raise Unavailable(
            f"Apple's revoke endpoint answered {answer.status_code}",
            evidence=UnavailableEvidence.HEALTH_CHECK_FAILING,
        )
    if answer.is_success:
        logger.info("identity.apple_sign_in.revoked", client_id=client_id)
        return Revocation.REVOKED
    error = _error_code(answer)
    if error == "invalid_grant":
        logger.info("identity.apple_sign_in.token_already_spent", client_id=client_id)
        return Revocation.ALREADY_SPENT
    logger.warning(
        "identity.apple_sign_in.revoke_refused",
        client_id=client_id,
        status_code=answer.status_code,
        apple_error=error,
    )
    return Revocation.REFUSED


async def revoke_apple_sign_in(
    user_id: str,
    *,
    config: IdentitySettings = identity_settings,
    http: httpx.AsyncClient | None = None,
) -> Revocation | None:
    """Revoke the Apple sign-in kept for ``user_id``; None if there is none.

    The record is dropped once Apple has answered, whatever it said, so a
    retried deletion does not ask twice. It stays while Apple is down, which
    is what lets the retry try again.
    """
    record = (await get_user_metadata(user_id)).metadata.get(REVOCATION_METADATA_KEY)
    if not isinstance(record, dict):
        return None
    kept = await _open(record)
    outcome: Revocation | None = None
    if kept is None:
        logger.warning(
            "identity.apple_sign_in.revocation_unreadable",
            reason="the kept Apple token could not be decrypted",
        )
    else:
        async with AsyncExitStack() as owned:
            client = http or await owned.enter_async_context(
                httpx.AsyncClient(timeout=10)
            )
            outcome = await revoke_refresh_token(
                config,
                client_id=kept.client_id,
                refresh_token=kept.refresh_token,
                http=client,
            )
    await update_user_metadata(user_id, {REVOCATION_METADATA_KEY: None})
    return outcome


async def _open(record: Mapping[str, object]) -> _Kept | None:
    """``client_id`` and ``refresh_token`` from a kept record, if it still opens."""
    client_id = record.get("client_id")
    sealed = record.get("sealed")
    if not isinstance(client_id, str) or not isinstance(sealed, dict):
        return None
    try:
        opened = await get_secret_cipher().decrypt_json_async(sealed)
    except InvalidToken, ValueError:
        return None
    refresh_token = (opened or {}).get("refresh_token")
    if not isinstance(refresh_token, str):
        return None
    return _Kept(client_id=client_id, refresh_token=refresh_token)


def _error_code(answer: httpx.Response) -> str | None:
    # Apple's documented `ErrorResponse` is `{"error": "<code>"}`. Anything else
    # is read as no code at all rather than echoed into a log.
    try:
        body = answer.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, str) and error.isidentifier() else None


def _retry_after(answer: httpx.Response) -> timedelta | None:
    raw = answer.headers.get("retry-after", "")
    return timedelta(seconds=int(raw)) if raw.isdigit() else None


__all__ = [
    "APPLE_REVOKE_ENDPOINT",
    "REVOCATION_METADATA_KEY",
    "Revocation",
    "apple_client_secret",
    "apple_private_key",
    "apple_provider",
    "remember_apple_refresh_token",
    "revoke_apple_sign_in",
    "revoke_refresh_token",
]
