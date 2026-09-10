"""When a stored credential is worth refreshing before using it.

Refreshing is not free: it re-reads the account, connector and auth config, then
makes a round trip to the provider. Doing that before every execution -- which is
what the previous code did, unconditionally -- put a full provider round trip in
front of every single connector call, wasted on any token that was still valid.

A credential with no expiry is never refreshed proactively. That covers API keys,
bot tokens, SQL passwords and any OAuth provider that reports no expiry, and it
is deliberate: for those, an expiry check can only ever say "no". Rejection is
handled reactively instead, on the 401, which is also the only thing that
notices a credential revoked at the provider.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.errors import AccountResolutionError


def _as_aware(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            # A malformed stored expiry must not take down every execution.
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def credential_expires_at(credentials: dict[str, Any] | None) -> datetime | None:
    """When this credential stops working, if the provider said.

    Public because expiry is not only a refresh trigger: anything that *copies*
    a token somewhere -- the workspace credential bridge writes one into a
    sandbox -- has to know how long its copy is good for.
    """
    return _as_aware((credentials or {}).get("expires_at"))


def credential_refresh_due(
    credentials: dict[str, Any] | None, *, now: datetime | None = None
) -> bool:
    """Whether ``credentials`` should be refreshed before the next call."""
    expires_at = _as_aware((credentials or {}).get("expires_at"))
    if expires_at is None:
        return False
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    skew = timedelta(
        seconds=connector_settings.connector_credential_refresh_skew_seconds
    )
    return expires_at <= reference + skew


async def resolve_execution_credentials(
    account: Any,
    user_id: Any,
    *,
    connector_service: Any,
    serialize: Callable[[Any], dict[str, Any]],
    is_oauth: Callable[[Any], bool],
) -> dict[str, Any]:
    """The credentials to execute with, refreshed only when actually due.

    The previous version refreshed on every execution. That is not just a
    provider round trip: ``get_account_credentials`` re-reads the account, the
    connector and the auth config first, and decrypts again. All of it was spent
    on tokens that were usually valid for another hour. A credential that is
    rejected anyway is refreshed reactively on the 401, which also catches
    revocation that no expiry check can see.
    """
    stored = serialize(account.credentials)
    if not connector_service or not is_oauth(account):
        return stored
    if not credential_refresh_due(stored):
        return stored

    refreshed = await connector_service.get_account_credentials(
        account.id, user_id, account.organization_id
    )
    refreshed_credentials = serialize(refreshed)
    if "user_data" not in refreshed_credentials and stored.get("user_data"):
        refreshed_credentials["user_data"] = stored["user_data"]
    return refreshed_credentials


def serialize_credentials(credentials: Any) -> dict[str, Any]:
    """A stored credential as a plain mapping, in either shape it arrives in.

    Callers hold a typed model on the OAuth paths and a plain mapping on the
    credential-managed ones.
    """
    if credentials is None:
        raise AccountResolutionError("Resolved account has no credentials.")
    if isinstance(credentials, dict):
        return credentials
    model_dump = getattr(credentials, "model_dump", None)
    if callable(model_dump):
        return model_dump(exclude_none=True)
    raise AccountResolutionError(
        "Resolved account credentials are in unsupported format."
    )


def is_oauth_account(account: Any) -> bool:
    """Whether this account holds something a refresh could renew.

    Decided from the credential's own shape, because that is the only authority
    available here: an auth scheme is a property of the install's `KindSpec`,
    not of the connector, and this is handed an account.
    """
    creds = getattr(account, "credentials", None)
    keys = ("access_token", "refresh_token", "connection_id")
    if isinstance(creds, dict):
        return any(key in creds for key in keys)
    return any(hasattr(creds, key) for key in keys)


async def fresh_credentials(
    account: Any, user_id: Any, *, connector_service: Any
) -> dict[str, Any]:
    """`resolve_execution_credentials` with this module's own shape helpers.

    The convenience form, so a caller that has no opinion about serialization
    does not have to supply one -- and, more to the point, so every path that
    reaches a stored token reaches it through the same refresh. The sandbox's
    `git`/`gh` bridge did not, and re-wrote an expired token into the workspace
    every time it was asked.
    """
    return await resolve_execution_credentials(
        account,
        user_id,
        connector_service=connector_service,
        serialize=serialize_credentials,
        is_oauth=is_oauth_account,
    )
