"""AES-256-GCM with associated data -- the one cipher every secret goes through.

Replaces Fernet, which is AES-128-CBC with an HMAC and has no associated data,
so a ciphertext could be copied from one tenant's row into another's and still
decrypt. Every call here takes ``aad``: the context the ciphertext is bound to.
Decrypting under any other context raises :class:`InvalidTag`.

Sealed form is ``nonce || ciphertext || tag`` with a random 96-bit nonce. NIST
SP 800-38D caps random nonces at 2^32 encryptions per key, which is why the
vault gives every secret version its own data key: each key seals once.
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12

__all__ = ["InvalidTag", "KEY_BYTES", "NONCE_BYTES", "new_key", "open_sealed", "seal"]


def new_key() -> bytes:
    """A fresh random 256-bit key."""
    return os.urandom(KEY_BYTES)


def seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    """Encrypt ``plaintext`` under ``key``, bound to ``aad``."""
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def open_sealed(key: bytes, sealed: bytes, aad: bytes) -> bytes:
    """Decrypt a value produced by :func:`seal`.

    Raises :class:`InvalidTag` when the key, the bytes, or the context differ
    from the ones it was sealed with -- including a truncated value, which is
    reported the same way rather than as an index error.
    """
    if len(sealed) <= NONCE_BYTES:
        raise InvalidTag()
    return AESGCM(key).decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], aad)
