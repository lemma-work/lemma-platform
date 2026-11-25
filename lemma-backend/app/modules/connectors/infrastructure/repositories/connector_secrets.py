"""Where the connectors module keeps its secrets in the vault, stated once.

An account's credentials, an install's config and an event subscription's
signing secret are each one vault secret. The purpose names the column the
secret stands in for, and the scope comes from the row that points at it --
never from the secret -- because both are bound into the ciphertext: a row
repointed at another tenant's secret gets a failed decryption, not that
tenant's tokens.

These must stay exactly what migration ``0078_vault_cutover`` used when it
copied the old Fernet columns over: a different purpose or scope here makes
every migrated secret undecryptable.

Kept apart from the repositories so the few other readers -- the backfill
script, the e2e tests that check what reached storage -- use the same purpose
strings and scopes instead of copying them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.modules.vault.contracts import JsonObject, SecretScope

ACCOUNT_CREDENTIALS_PURPOSE = "connectors.account.credentials"
AUTH_CONFIG_PURPOSE = "connectors.auth_config.config"
EVENT_SUBSCRIPTION_SECRET_PURPOSE = "connectors.event_subscription.secret"
ACCOUNTS_TABLE = "accounts"
AUTH_CONFIGS_TABLE = "auth_configs"
EVENT_SUBSCRIPTIONS_TABLE = "connector_event_subscriptions"

# Where a credential says when its token dies: OAuth credentials call it
# `expires_at`, Composio's cached token `token_expires_at`.
_EXPIRY_KEYS = ("expires_at", "token_expires_at")


def account_scope(organization_id: UUID, user_id: UUID) -> SecretScope:
    """An account's credentials belong to one person in one organization."""
    return SecretScope(organization_id=organization_id, user_id=user_id)


def auth_config_scope(organization_id: UUID) -> SecretScope:
    """An install's config belongs to the organization, not to whoever wrote it."""
    return SecretScope(organization_id=organization_id)


def event_subscription_scope(organization_id: UUID, user_id: UUID) -> SecretScope:
    """A subscription's secret belongs to the person whose account made it."""
    return SecretScope(organization_id=organization_id, user_id=user_id)


def serialize_credentials(credentials: object | None) -> JsonObject | None:
    """Credentials as the JSON object stored in the vault, or ``None`` for none.

    Accepts the typed credential models and plain dicts alike: callers hand over
    either, and both must land as the same shape. An empty mapping is stored as
    it is -- "connected with no fields" is a real state for a few connectors.
    """
    if credentials is None:
        return None
    model_dump = getattr(credentials, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else None
    if isinstance(credentials, dict):
        return credentials
    return None


def credentials_expiry(value: JsonObject | None) -> datetime | None:
    """When the credential's token expires, if it says and it parses.

    Recorded on the vault row so expiry can be read without decrypting; parsed
    the way the migration parsed it (ISO strings, epoch seconds or
    milliseconds). A value that does not parse is not an error: the credential
    still works, the vault just does not know when it stops.
    """
    if not value:
        return None
    for key in _EXPIRY_KEYS:
        parsed = _parse_expiry(value.get(key))
        if parsed is not None:
            return parsed
    return None


def _parse_expiry(raw: object) -> datetime | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        seconds = raw / 1000 if raw > 10**12 else raw
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except OverflowError, OSError, ValueError:
            return None
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        # Providers hand back naive timestamps as often as not; they are UTC.
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None
