"""Stateless cryptography shared across the backend.

- :mod:`app.core.crypto.aead` / :mod:`~app.core.crypto.aad` -- AES-256-GCM with
  canonical associated data. The only cipher secrets go through.
- :mod:`app.core.crypto.roots` -- the root key (Cloud KMS, a keyset, the OS
  keychain) that protects the vault's key-encryption keys.
- :func:`get_secret_signer` -- HMAC signing of short-lived tokens with
  per-purpose, rotatable keys.

Secrets themselves are stored by the vault module
(``app.modules.vault.contracts``); nothing here touches the database.
"""

from __future__ import annotations

from app.core.crypto.factory import get_secret_signer, reset_crypto_caches
from app.core.crypto.ports import Keyring, KeyMaterial, SecretSigner

__all__ = [
    "Keyring",
    "KeyMaterial",
    "SecretSigner",
    "get_secret_signer",
    "reset_crypto_caches",
]
