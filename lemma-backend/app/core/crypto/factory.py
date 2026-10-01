"""The cached signer.

Encryption at rest is no longer here: secrets live in the vault module, and the
keys that protect them come from :mod:`app.core.crypto.roots`.

Call :func:`reset_crypto_caches` after changing key configuration in-process.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.crypto.ports import SecretSigner
from app.core.crypto.roots.factory import reset_root_key_provider
from app.core.crypto.signer import HkdfSecretSigner
from app.core.crypto.signing_keys import signing_keyring


@lru_cache(maxsize=1)
def get_secret_signer() -> SecretSigner:
    return HkdfSecretSigner(signing_keyring)


def reset_crypto_caches() -> None:
    """Drop cached singletons so the next call re-reads configuration."""
    get_secret_signer.cache_clear()
    reset_root_key_provider()
