"""Protocols and value types for the crypto facility.

Three concepts, one backend selection:

- :class:`KeyProvider` — the pluggable "KMS". Supplies and protects key material
  and exposes the current *primary* key id. Two crypto strategies are supported:
    * ``encryption_alg == "fernet"`` — the provider yields a raw Fernet
      :class:`Keyring` and crypto happens in-process (static / keychain /
      secret_manager).
    * ``encryption_alg == "kms+fernet"`` — the provider wraps/unwraps a
      per-record data key (DEK) via a remote KEK that never leaves the backend
      (gcp_kms). The DEK then encrypts the payload locally with Fernet.
  Every provider also yields a :meth:`KeyProvider.signing_keyring` of raw key
  material used as HKDF input for HMAC signing (signing must stay local — it is
  on hot request paths).

- :class:`SecretCipher` — encrypt/decrypt secrets at rest (versioned, key-id
  tagged envelopes). Implemented by ``EnvelopeSecretCipher``.

- :class:`SecretSigner` — sign/verify short-lived tokens, deriving a per-purpose
  subkey from the root key via HKDF. Implemented by ``HkdfSecretSigner``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class KeyMaterial:
    """A single named symmetric key.

    ``secret`` is a urlsafe-base64 Fernet key (the 44-char form passed straight
    to ``cryptography.fernet.Fernet``). The same bytes are used as HKDF input
    material for signing.
    """

    kid: str
    secret: bytes


@dataclass(frozen=True)
class Keyring:
    """An ordered set of keys with one designated *primary* (used for new writes).

    Non-primary keys are retained so data/tokens produced under a previous key
    stay readable/verifiable until they are rotated forward (or the grace window
    for signing elapses).
    """

    primary_kid: str
    keys: dict[str, KeyMaterial]

    def __post_init__(self) -> None:
        if self.primary_kid not in self.keys:
            raise ValueError(
                f"primary_kid {self.primary_kid!r} is not present in the keyring"
            )

    @property
    def primary(self) -> KeyMaterial:
        return self.keys[self.primary_kid]

    def get(self, kid: str) -> KeyMaterial | None:
        return self.keys.get(kid)


class KeyProvider(ABC):
    """Pluggable key backend ("KMS")."""

    #: Stable short name for logs/diagnostics (e.g. "static", "gcp_kms").
    name: str = "key-provider"

    @property
    @abstractmethod
    def encryption_alg(self) -> str:
        """``"fernet"`` (local keyring) or ``"kms+fernet"`` (envelope)."""

    @property
    @abstractmethod
    def primary_kid(self) -> str:
        """Key id used to stamp new ciphertext."""

    def encryption_keyring(self) -> Keyring | None:
        """Raw Fernet keyring for in-process crypto.

        ``None`` for envelope-only providers (which use :meth:`wrap_dek` /
        :meth:`unwrap_dek` instead).
        """
        return None

    def wrap_dek(self, dek: bytes) -> tuple[str, bytes]:
        """Wrap a data key with the remote KEK; return ``(kek_kid, wrapped)``.

        Only implemented by envelope providers (``encryption_alg ==
        "kms+fernet"``).
        """
        raise NotImplementedError(
            f"{self.name} does not support envelope encryption (wrap_dek)"
        )

    def unwrap_dek(self, kid: str, wrapped: bytes) -> bytes:
        """Unwrap a previously wrapped data key. Envelope providers only."""
        raise NotImplementedError(
            f"{self.name} does not support envelope encryption (unwrap_dek)"
        )

    @abstractmethod
    def signing_keyring(self) -> Keyring:
        """Raw keyring used as HKDF input for HMAC signing (all providers)."""


@runtime_checkable
class SecretCipher(Protocol):
    """Encrypt/decrypt secrets at rest.

    ``encrypt_json`` / ``decrypt_json`` keep the exact signatures of the legacy
    ``SecretEncryptionPort`` so repositories need no changes. ``*_str`` variants
    handle plain string columns (e.g. webhook secrets).
    """

    def encrypt_json(self, value: dict[str, Any] | None) -> dict[str, Any] | None: ...

    def decrypt_json(self, value: dict[str, Any] | None) -> dict[str, Any] | None: ...

    # Async variants offload the (possibly KMS-backed, blocking) crypto off the
    # event loop; used by repositories that run on the worker loop.
    async def encrypt_json_async(
        self, value: dict[str, Any] | None
    ) -> dict[str, Any] | None: ...

    async def decrypt_json_async(
        self, value: dict[str, Any] | None
    ) -> dict[str, Any] | None: ...

    def encrypt_str(self, value: str | None) -> str | None: ...

    def decrypt_str(self, value: str | None) -> str | None: ...

    def is_under_primary_key(self, value: Any) -> bool:
        """True when ``value`` is already encrypted under the current primary key.

        Used by the rotation walker to skip rows that need no re-encryption.
        """
        ...


@runtime_checkable
class SecretSigner(Protocol):
    """Sign/verify short-lived tokens with per-purpose, rotatable keys."""

    def sign(self, purpose: str, payload: bytes) -> str:
        """Return a ``"<kid>.<sig_b64>"`` signature for ``payload``."""
        ...

    def verify(self, purpose: str, payload: bytes, signature: str) -> bool:
        """Verify a signature minted by :meth:`sign` (or a legacy 1-part sig)."""
        ...
