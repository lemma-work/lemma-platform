"""The root key: the one key that never touches Postgres.

The vault keeps its key-encryption keys (KEKs) in the database, wrapped by a
root key that lives somewhere else -- Cloud KMS, an OS keychain, or a keyset
the operator supplies. A database dump alone therefore decrypts nothing.

A root is used a handful of times per process: once per KEK at start, and when
a KEK is created or rotated. It is never on the path of reading a secret.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class RootKeyError(RuntimeError):
    """The root key could not wrap or unwrap. Always fatal for the caller.

    Messages say what to change (a missing IAM role, an unset key name), never
    anything about the key material.
    """


@dataclass(frozen=True, slots=True)
class WrappedKey:
    """A key sealed by the root.

    ``root_ref`` names the root key (and version) that sealed it, so the right
    one can open it after the root has rotated.
    """

    root_ref: str
    blob: bytes


class RootKeyProvider(ABC):
    #: Stable short name for logs and ``vault_keys.root_provider``.
    name: str = "root"

    @abstractmethod
    async def wrap(self, key: bytes, *, aad: bytes) -> WrappedKey:
        """Seal ``key`` under the current root key, bound to ``aad``."""

    @abstractmethod
    async def unwrap(self, root_ref: str, blob: bytes, *, aad: bytes) -> bytes:
        """Open a key sealed by :meth:`wrap` with the root named by ``root_ref``."""

    def needs_rewrap(self, root_ref: str) -> bool:
        """True when ``root_ref`` is no longer the root new wraps would use."""
        return False

    async def probe(self) -> str:
        """Round-trip a throwaway key; return the ref it was sealed under."""
        canary = b"\x00" * 32
        wrapped = await self.wrap(canary, aad=b"lemma-vault/probe")
        if (
            await self.unwrap(wrapped.root_ref, wrapped.blob, aad=b"lemma-vault/probe")
            != canary
        ):
            raise RootKeyError(f"{self.name} root returned a different key on unwrap")
        return wrapped.root_ref
