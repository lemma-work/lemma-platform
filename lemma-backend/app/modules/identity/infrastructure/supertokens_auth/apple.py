"""Sign in with Apple: the provider, its client secret, and revoking it.

Apple asks an app that offers Sign in with Apple to revoke the person's
authorization when they delete their account (its technote TN3194). Revoking
spends a token Apple issued, and the refresh token from the code exchange is
the only one that outlives the sign-in. So it is kept as a vault secret bound
to the user, and the SuperTokens user's own metadata names it (with the client
it was issued to) -- which needs no table of ours and goes when that user does.
The deletion's work item spends it just before it deletes the user, and
deletes the secret once Apple has answered. A user who signed in before the
vault has the token sealed with the old secret cipher in the metadata itself;
that is still read, and the next sign-in moves it into the vault.

The phone app signs in with Apple natively rather than through the web's
redirect. Apple issues that sign-in to the app's bundle id, so the provider
carries a second client for it, and the phone names it (`clientType: "ios"`)
when it posts the id token to SuperTokens' `/signinup`. A native sign-in brings
no refresh token, only a one-time authorization code beside the id token; it is
traded for one here, so a phone's account is revoked on deletion like a web
one.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import timedelta
from enum import StrEnum
from typing import NamedTuple
from uuid import uuid7

import httpx
import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from supertokens_python.exceptions import BadInputError
from supertokens_python.recipe.thirdparty.exceptions import ClientTypeNotFoundError
from supertokens_python.recipe.thirdparty.provider import (
    Provider,
    ProviderClientConfig,
    ProviderConfig,
    ProviderConfigForClient,
    ProviderInput,
)
from supertokens_python.recipe.usermetadata.asyncio import (
    get_user_metadata,
    update_user_metadata,
)

from app.core.config import reveal_secret
from app.core.infrastructure.work.types import Unavailable, UnavailableEvidence
from app.core.log.log import get_logger
from app.modules.identity.config import IdentitySettings, identity_settings
from app.modules.identity.infrastructure.supertokens_auth import apple_refresh_tokens
from app.modules.identity.infrastructure.supertokens_auth.apple_refresh_tokens import (
    REFRESH_TOKEN_PURPOSE,
)

logger = get_logger(__name__)

# Apple's published endpoints, not choices a deployment makes -- constants for
# the reason Telegram's are (`identity/config.py`).
APPLE_AUDIENCE = "https://appleid.apple.com"
APPLE_REVOKE_ENDPOINT = "https://appleid.apple.com/auth/revoke"
APPLE_TOKEN_ENDPOINT = "https://appleid.apple.com/auth/token"

#: The provider's two clients, as a sign-in names them (SuperTokens'
#: `clientType`). The web names none, so naming none is the web's.
WEB_CLIENT_TYPE = "web"
NATIVE_CLIENT_TYPE = "ios"

#: Where a native sign-in puts Apple's one-time authorization code, beside the
#: id token in `oAuthTokens`.
NATIVE_CODE_KEY = "authorization_code"

#: Apple answers in a second or two; a sign-in waits no longer than this.
_APPLE_TIMEOUT_SECONDS = 10

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


def is_apple_sign_in_available(config: IdentitySettings) -> bool:
    """Whether Apple is registered: the four values set, and the key loads.

    The test `apple_provider` registers on, and so the one that decides the
    button. Anything that sends a person to Apple asks this rather than
    `is_apple_oauth_configured`, which counts a key as set even when it does
    not load -- and a person sent to a button that is not there has no way in.
    """
    return config.is_apple_oauth_configured() and apple_private_key(config) is not None


def apple_app_bundle_id(config: IdentitySettings) -> str | None:
    """The phone app's bundle id, when one is set."""
    return (config.apple_app_bundle_id or "").strip() or None


def apple_provider(config: IdentitySettings) -> ProviderInput | None:
    """Sign in with Apple, or None until all four values are set and usable.

    The web's client always; the phone's beside it once its bundle id is set.
    It is signed with the same team, key id and key, and never registered
    without the web's, so the phone is never offered Apple where the web is
    not.
    """
    if not is_apple_sign_in_available(config):
        return None
    private_key = apple_private_key(config)
    assert private_key is not None and config.apple_client_id is not None
    signing = {
        "teamId": config.apple_team_id,
        "keyId": config.apple_key_id,
        "privateKey": private_key,
    }
    # SuperTokens asks for openid and email only. Apple sends the name when
    # asked, and only on the first sign-in, so asking later cannot recover it.
    scope = ["openid", "email", "name"]
    clients = [
        ProviderClientConfig(
            client_type=WEB_CLIENT_TYPE,
            client_id=config.apple_client_id,
            scope=scope,
            additional_config=signing,
        ),
    ]
    bundle_id = apple_app_bundle_id(config)
    if bundle_id is not None:
        # The id token a phone gets is issued to the bundle id, and verified
        # against it: with only the Services ID registered it would not be.
        clients.append(
            ProviderClientConfig(
                client_type=NATIVE_CLIENT_TYPE,
                client_id=bundle_id,
                scope=scope,
                additional_config=signing,
            )
        )
    return ProviderInput(
        config=ProviderConfig(
            # SuperTokens picks its Apple implementation by this id: OIDC
            # discovery, `response_mode=form_post`, and the client secret
            # signed from the three values above.
            third_party_id="apple",
            clients=clients,
        ),
        override=_web_unless_named,
    )


def _web_unless_named(provider: Provider) -> Provider:
    """The web's client when a call names none; a 400 for one not registered.

    With more than one client SuperTokens refuses any call that does not name
    one, and the web's sign-in -- every browser holding an older page too --
    names none. A client that is not registered (the phone's, before its
    bundle id is set) is the caller's mistake, not a 500: it is also how the
    phone asks whether to offer the button.
    """
    choose = provider.get_config_for_client_type

    async def get_config_for_client_type(
        client_type: str | None, user_context: dict[str, object]
    ) -> ProviderConfigForClient:
        try:
            return await choose(client_type or WEB_CLIENT_TYPE, user_context)
        except ClientTypeNotFoundError as missing:
            raise BadInputError(missing.message) from missing

    provider.get_config_for_client_type = get_config_for_client_type
    return provider


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


async def with_native_refresh_token(
    oauth_tokens: Mapping[str, object],
    id_token_payload: Mapping[str, object] | None,
    *,
    config: IdentitySettings = identity_settings,
    http: httpx.AsyncClient | None = None,
) -> Mapping[str, object]:
    """``oauth_tokens`` with a refresh token, traded for a phone's code.

    A native sign-in sends the verified id token and Apple's one-time
    authorization code, never a refresh token. The code is traded here with a
    client secret signed for the bundle id -- the only client it was issued to
    -- so the sign-in can be revoked later like a web one. Anything short of
    a refresh token for the same person returns the tokens as they came: the
    sign-in goes ahead, and :func:`remember_apple_refresh_token` logs that
    nothing was kept.
    """
    if oauth_tokens.get("refresh_token"):
        return oauth_tokens
    code = oauth_tokens.get(NATIVE_CODE_KEY)
    payload = id_token_payload or {}
    bundle_id = apple_app_bundle_id(config)
    if not (isinstance(code, str) and code and bundle_id):
        return oauth_tokens
    if payload.get("aud") != bundle_id:
        return oauth_tokens
    client_secret = apple_client_secret(config, bundle_id)
    if client_secret is None:
        return oauth_tokens
    form = {
        "client_id": bundle_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    }
    try:
        if http is not None:
            answer = await http.post(APPLE_TOKEN_ENDPOINT, data=form)
        else:
            async with httpx.AsyncClient(timeout=_APPLE_TIMEOUT_SECONDS) as client:
                answer = await client.post(APPLE_TOKEN_ENDPOINT, data=form)
    except httpx.HTTPError as unreachable:
        logger.warning(
            "identity.apple_sign_in.code_not_traded",
            reason=f"Apple's token endpoint unreachable: {type(unreachable).__name__}",
        )
        return oauth_tokens
    if not answer.is_success:
        logger.warning(
            "identity.apple_sign_in.code_not_traded",
            reason="Apple refused the code",
            status_code=answer.status_code,
            apple_error=_error_code(answer),
        )
        return oauth_tokens
    traded = _json_object(answer)
    refresh_token = traded.get("refresh_token")
    if not (isinstance(refresh_token, str) and refresh_token):
        logger.warning(
            "identity.apple_sign_in.code_not_traded",
            reason="Apple's answer held no refresh token",
        )
        return oauth_tokens
    # The code is the phone's word, the id token is verified: a code someone
    # else signed in with must not be kept on this account, where deleting it
    # would revoke theirs. Apple's own answer names whose code it was.
    if _subject_of(traded.get("id_token")) != payload.get("sub"):
        logger.warning(
            "identity.apple_sign_in.code_not_traded",
            reason="the code was issued to someone else",
        )
        return oauth_tokens
    return {**oauth_tokens, "refresh_token": refresh_token}


async def remember_apple_refresh_token(
    user_id: str,
    oauth_tokens: Mapping[str, object],
    id_token_payload: Mapping[str, object] | None,
) -> bool:
    """Keep what revoking this sign-in will need; False when Apple gave none.

    The client id comes from the verified id token's ``aud`` rather than from
    settings: a refresh token can only be revoked by the client it was issued
    to, and a phone's native sign-in is issued to the app's bundle id.

    A sign-in that brought nothing to keep still goes ahead -- a phone that
    sends only the id token never has a refresh token -- but it is logged with
    the user, so the accounts a deletion cannot revoke can be found.
    """
    refresh_token = oauth_tokens.get("refresh_token")
    client_id = (id_token_payload or {}).get("aud")
    if not (isinstance(refresh_token, str) and refresh_token):
        return _not_kept(user_id, "Apple sent no refresh token")
    if not (isinstance(client_id, str) and client_id):
        return _not_kept(user_id, "the id token named no audience")
    kept = (await get_user_metadata(user_id)).metadata.get(REVOCATION_METADATA_KEY)
    previous = (
        apple_refresh_tokens.secret_id_of(kept) if isinstance(kept, Mapping) else None
    )
    # The record first, naming the secret before it exists: if keeping the
    # token then fails, the sign-in fails and its retry keeps it under the same
    # id -- no secret is ever left that nothing names.
    secret_id = previous or uuid7()
    await update_user_metadata(
        user_id,
        {
            REVOCATION_METADATA_KEY: {
                "client_id": client_id,
                "secret_id": str(secret_id),
            }
        },
    )
    await apple_refresh_tokens.keep(user_id, secret_id, refresh_token)
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
    kept = await _open(user_id, record)
    outcome: Revocation | None = None
    if kept is None:
        logger.warning(
            "identity.apple_sign_in.revocation_unreadable",
            reason="the kept Apple token could not be decrypted",
        )
    elif http is not None:
        outcome = await revoke_refresh_token(
            config,
            client_id=kept.client_id,
            refresh_token=kept.refresh_token,
            http=http,
        )
    else:
        async with httpx.AsyncClient(timeout=10) as client:
            outcome = await revoke_refresh_token(
                config,
                client_id=kept.client_id,
                refresh_token=kept.refresh_token,
                http=client,
            )
    secret_id = apple_refresh_tokens.secret_id_of(record)
    if secret_id is not None:
        # Before the record: a record left naming a deleted secret is dropped
        # by the retry, while a secret named by nothing would never go.
        await apple_refresh_tokens.forget(user_id, secret_id)
    await update_user_metadata(user_id, {REVOCATION_METADATA_KEY: None})
    return outcome


def _not_kept(user_id: str, reason: str) -> bool:
    logger.warning(
        "identity.apple_sign_in.token_not_kept", user_id=user_id, reason=reason
    )
    return False


async def _open(user_id: str, record: Mapping[str, object]) -> _Kept | None:
    """``client_id`` and ``refresh_token`` from a kept record, if it still opens."""
    client_id = record.get("client_id")
    if not isinstance(client_id, str):
        return None
    secret_id = apple_refresh_tokens.secret_id_of(record)
    if secret_id is not None:
        refresh_token = await apple_refresh_tokens.reveal(user_id, secret_id)
    else:
        refresh_token = await apple_refresh_tokens.open_legacy(record)
    if refresh_token is None:
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


def _json_object(answer: httpx.Response) -> Mapping[str, object]:
    try:
        body = answer.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _subject_of(id_token: object) -> object:
    """``sub`` of an id token Apple's token endpoint just answered with.

    Not verified: it came from Apple over TLS in answer to this server's own
    request, which is all the signature would prove.
    """
    if not isinstance(id_token, str):
        return None
    try:
        claims = jwt.decode(id_token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return None
    return claims.get("sub") if isinstance(claims, dict) else None


def _retry_after(answer: httpx.Response) -> timedelta | None:
    raw = answer.headers.get("retry-after", "")
    return timedelta(seconds=int(raw)) if raw.isdigit() else None


__all__ = [
    "APPLE_REVOKE_ENDPOINT",
    "APPLE_TOKEN_ENDPOINT",
    "NATIVE_CLIENT_TYPE",
    "NATIVE_CODE_KEY",
    "REFRESH_TOKEN_PURPOSE",
    "REVOCATION_METADATA_KEY",
    "Revocation",
    "WEB_CLIENT_TYPE",
    "apple_app_bundle_id",
    "apple_client_secret",
    "apple_private_key",
    "apple_provider",
    "is_apple_sign_in_available",
    "remember_apple_refresh_token",
    "revoke_apple_sign_in",
    "revoke_refresh_token",
    "with_native_refresh_token",
]
