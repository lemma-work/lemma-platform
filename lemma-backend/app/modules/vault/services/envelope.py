"""How a secret is sealed: a fresh data key per value, wrapped by a KEK.

    wrapped_dek = AES-GCM(kek, dek,   aad = "dek"    | secret id | kek id)
    ciphertext  = AES-GCM(dek, value, aad = "secret" | secret id | version | kind
                                            | purpose | org | pod | user)

The value's associated data is the scope the *caller* expects. Pointing an
owner row at another tenant's secret, or at a secret minted for another
purpose, fails authentication instead of returning the value.
"""

from __future__ import annotations

from uuid import UUID

from app.core.crypto.aad import encode_aad
from app.core.crypto.aead import new_key, open_sealed, seal

from app.modules.vault.domain.types import SecretKind, SecretScope

ALG = "A256GCM"


def _id(value: UUID | None) -> str:
    return "" if value is None else str(value)


def dek_aad(secret_id: UUID, kek_id: UUID) -> bytes:
    return encode_aad("lemma-vault/dek/v1", str(secret_id), str(kek_id))


def value_aad(
    secret_id: UUID,
    version: int,
    kind: SecretKind,
    purpose: str,
    scope: SecretScope,
) -> bytes:
    return encode_aad(
        "lemma-vault/secret/v1",
        str(secret_id),
        str(version),
        kind.value,
        purpose,
        _id(scope.organization_id),
        _id(scope.pod_id),
        _id(scope.user_id),
    )


def seal_value(
    *,
    kek_id: UUID,
    kek: bytes,
    secret_id: UUID,
    version: int,
    kind: SecretKind,
    purpose: str,
    scope: SecretScope,
    payload: bytes,
) -> tuple[bytes, bytes]:
    """Return ``(wrapped_dek, ciphertext)`` for a new version of a secret."""
    dek = new_key()
    wrapped = seal(kek, dek, dek_aad(secret_id, kek_id))
    sealed = seal(dek, payload, value_aad(secret_id, version, kind, purpose, scope))
    return wrapped, sealed


def open_value(
    *,
    kek_id: UUID,
    kek: bytes,
    secret_id: UUID,
    version: int,
    kind: SecretKind,
    purpose: str,
    scope: SecretScope,
    wrapped_dek: bytes,
    ciphertext: bytes,
) -> bytes:
    """Raises ``InvalidTag`` for any mismatch in key, bytes or context."""
    dek = open_sealed(kek, wrapped_dek, dek_aad(secret_id, kek_id))
    return open_sealed(
        dek, ciphertext, value_aad(secret_id, version, kind, purpose, scope)
    )


def rewrap_dek(
    *,
    secret_id: UUID,
    old_kek_id: UUID,
    old_kek: bytes,
    new_kek_id: UUID,
    new_kek: bytes,
    wrapped_dek: bytes,
) -> bytes:
    """Move a data key to a new KEK. The value's ciphertext is untouched."""
    dek = open_sealed(old_kek, wrapped_dek, dek_aad(secret_id, old_kek_id))
    return seal(new_kek, dek, dek_aad(secret_id, new_kek_id))
