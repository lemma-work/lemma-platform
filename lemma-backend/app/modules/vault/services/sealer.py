"""Sealing short-lived values that are not stored as secrets.

Some secret-bearing values live briefly outside the vault table: a sandbox's
environment cached in Redis, a GitHub installation token cached in Redis, the
MCP frame in a queued agent-host command. They get the same protection -- a
fresh data key wrapped by the active KEK, the value bound to where it is
stored -- in one self-contained string instead of a row.

    "lvs1:" + base64url( kek_id(16) || wrapped_dek(60) || kind(1) || ciphertext )

``bindings`` are the context the value belongs to (a cache key, a run id). A
sealed value moved to another key or run does not open.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.core.crypto.aad import encode_aad
from app.core.crypto.aead import InvalidTag, new_key, open_sealed, seal

from app.modules.vault.domain.types import (
    JsonObject,
    Revealed,
    SecretKind,
    SecretValue,
    encode_value,
)
from app.modules.vault.services.runtime import get_vault_keyring

PREFIX = "lvs1:"
_WRAPPED_DEK_BYTES = 12 + 32 + 16
_KINDS = {b"t": SecretKind.TEXT, b"j": SecretKind.JSON}


class SealedValueInvalid(ValueError):
    """Not a sealed value, or sealed for somewhere else. Treat as absent."""


class SealingKeys(Protocol):
    """The two keyring calls sealing needs. The process keyring by default."""

    async def active_encryption_key(self) -> tuple[UUID, bytes]: ...

    async def encryption_key(self, key_id: UUID) -> bytes: ...


def _aad(purpose: str, kind: SecretKind, bindings: Sequence[str]) -> bytes:
    return encode_aad("lemma-vault/sealed/v1", purpose, kind.value, *bindings)


def is_sealed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


async def seal_value(
    value: SecretValue,
    *,
    purpose: str,
    bindings: Sequence[str],
    keyring: SealingKeys | None = None,
) -> str:
    kind, payload = encode_value(value)
    kek_id, kek = await (keyring or get_vault_keyring()).active_encryption_key()
    dek = new_key()
    wrapped = seal(kek, dek, encode_aad("lemma-vault/sealed-dek/v1", str(kek_id)))
    sealed = seal(dek, payload, _aad(purpose, kind, bindings))
    tag = b"t" if kind is SecretKind.TEXT else b"j"
    raw = kek_id.bytes + wrapped + tag + sealed
    return PREFIX + base64.urlsafe_b64encode(raw).decode("ascii")


async def open_value(
    token: str,
    *,
    purpose: str,
    bindings: Sequence[str],
    keyring: SealingKeys | None = None,
) -> SecretValue:
    if not is_sealed(token):
        raise SealedValueInvalid("not a sealed value")
    try:
        raw = base64.urlsafe_b64decode(token[len(PREFIX) :])
    except ValueError as exc:
        raise SealedValueInvalid("sealed value is not valid base64") from exc
    header = 16 + _WRAPPED_DEK_BYTES
    if len(raw) <= header + 1 or raw[header : header + 1] not in _KINDS:
        raise SealedValueInvalid("sealed value is truncated")
    kek_id = UUID(bytes=raw[:16])
    kind = _KINDS[raw[header : header + 1]]
    kek = await (keyring or get_vault_keyring()).encryption_key(kek_id)
    try:
        dek = open_sealed(
            kek, raw[16:header], encode_aad("lemma-vault/sealed-dek/v1", str(kek_id))
        )
        payload = open_sealed(dek, raw[header + 1 :], _aad(purpose, kind, bindings))
    except InvalidTag as exc:
        raise SealedValueInvalid("sealed value does not open in this context") from exc
    return Revealed(kek_id, 0, kind, None, payload).value()


async def open_json(
    token: str,
    *,
    purpose: str,
    bindings: Sequence[str],
    keyring: SealingKeys | None = None,
) -> JsonObject:
    value = await open_value(token, purpose=purpose, bindings=bindings, keyring=keyring)
    if not isinstance(value, dict):
        raise SealedValueInvalid("sealed value is text, expected JSON")
    return value


async def open_text(
    token: str,
    *,
    purpose: str,
    bindings: Sequence[str],
    keyring: SealingKeys | None = None,
) -> str:
    value = await open_value(token, purpose=purpose, bindings=bindings, keyring=keyring)
    if not isinstance(value, str):
        raise SealedValueInvalid("sealed value is JSON, expected text")
    return value
