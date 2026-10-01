"""Where a connector's secrets live in the vault, stated once.

An account's credentials and an install's config are each one vault secret. The
purpose names the column the secret stands in for, and the scope comes from the
row that points at it -- never from the secret -- because both are bound into
the ciphertext: a row repointed at another tenant's secret gets a failed
decryption, not that tenant's tokens.

Kept apart from the repositories so the few other readers -- the historical
backfill script, the e2e tests that check what reached storage -- use the same
purpose strings and scopes instead of copying them, which is how a reader ends
up asking for a scope the writer never used.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.modules.vault.contracts import JsonObject, SecretScope

ACCOUNT_CREDENTIALS_PURPOSE = "connectors.account.credentials"
AUTH_CONFIG_PURPOSE = "connectors.auth_config.config"
ACCOUNTS_TABLE = "accounts"
AUTH_CONFIGS_TABLE = "auth_configs"

# Where a credential says when its token dies: OAuth credentials call it
# `expires_at`, Composio's cached token `token_expires_at`.
_EXPIRY_KEYS = ("expires_at", "token_expires_at")


def account_scope(organization_id: UUID, user_id: UUID) -> SecretScope:
    """An account's credentials belong to one person in one organization."""
    return SecretScope(organization_id=organization_id, user_id=user_id)


def auth_config_scope(organization_id: UUID) -> SecretScope:
    """An install's config belongs to the organization, not to whoever wrote it."""
    return SecretScope(organization_id=organization_id)


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

    Recorded on the vault row so expiry can be read without decrypting. A value
    that does not parse is not an error: the credential still works, the vault
    just does not know when it stops.
    """
    if not value:
        return None
    for key in _EXPIRY_KEYS:
        raw = value.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            continue
        # Providers hand back naive timestamps as often as not; they are UTC.
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None
