"""What other modules use to keep secrets.

A module that stores a secret keeps a ``*_secret_id`` column pointing at
``vault_secrets.id``, declares it with :func:`vault_owned`, and reads and writes
through :func:`vault_for` on its own unit of work::

    vault = vault_for(uow)
    ref = await vault.put(scope=SecretScope(org_id), purpose=PURPOSE,
                          value={"api_key": key}, owner_table="widgets")
    row.api_key_secret_id = ref.id
    ...
    creds = (await vault.reveal(row.api_key_secret_id,
                                expect=SecretScope(row.organization_id),
                                purpose=PURPOSE)).json()

``expect`` comes from the owner row, never from the secret; ``purpose`` names
the column. Both are bound into the ciphertext.
"""

from app.modules.vault.domain.errors import (
    SecretIntegrityError,
    SecretNotFound,
    SecretScopeMismatch,
    SecretVersionConflict,
    VaultError,
    VaultUnavailable,
)
from app.modules.vault.domain.types import (
    ActorKind,
    AuditActor,
    JsonObject,
    JsonValue,
    LeaseToken,
    Revealed,
    SecretKind,
    SecretMeta,
    SecretRef,
    SecretScope,
    SecretValue,
)
from app.modules.vault.domain.ports import KEEP, KeepExpiry, Vault
from app.modules.vault.infrastructure.models import vault_owned
from app.modules.vault.services.sealer import (
    SealedValueInvalid,
    SealingKeys,
    open_value,
    is_sealed,
    open_json,
    open_text,
    seal_value,
)
from app.modules.vault.services.store import SqlVault, reveal_values, vault_for

__all__ = [
    "KEEP",
    "ActorKind",
    "KeepExpiry",
    "AuditActor",
    "JsonObject",
    "JsonValue",
    "LeaseToken",
    "Revealed",
    "SealedValueInvalid",
    "SealingKeys",
    "SecretIntegrityError",
    "SecretKind",
    "SecretMeta",
    "SecretNotFound",
    "SecretRef",
    "SecretScope",
    "SecretScopeMismatch",
    "SecretValue",
    "SecretVersionConflict",
    "SqlVault",
    "Vault",
    "VaultError",
    "VaultUnavailable",
    "is_sealed",
    "open_json",
    "open_text",
    "open_value",
    "reveal_values",
    "seal_value",
    "vault_for",
    "vault_owned",
]
