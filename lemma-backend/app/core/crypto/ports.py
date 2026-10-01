"""Value types and the signer protocol.

- :class:`Keyring` / :class:`KeyMaterial` -- a named set of keys with one
  primary, used for a local root keyset and for signing.
- :class:`SecretSigner` -- sign/verify short-lived tokens, deriving a
  per-purpose subkey via HKDF. Implemented by ``HkdfSecretSigner``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


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


@runtime_checkable
class SecretSigner(Protocol):
    """Sign/verify short-lived tokens with per-purpose, rotatable keys."""

    def sign(self, purpose: str, payload: bytes) -> str:
        """Return a ``"<kid>.<sig_b64>"`` signature for ``payload``."""
        ...

    def verify(self, purpose: str, payload: bytes, signature: str) -> bool:
        """Verify a signature minted by :meth:`sign` (or a legacy 1-part sig)."""
        ...
