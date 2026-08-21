"""HMAC signer with per-purpose, rotatable keys.

One root key serves every signing purpose (widget embed tokens, datastore file
URLs, …): each purpose gets its own subkey via ``HKDF(root, info=purpose)`` so a
leak in one context can't forge another. Signatures are emitted as
``"<kid>.<sig>"`` — the embedded key id lets ``verify`` pick the right subkey and
keeps retired keys usable for a rotation grace window.

Tokens minted before unification (no ``kid``) are *not* handled here — call sites
keep a small legacy fallback for their grace window (see Phase 4 refactor).
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.crypto.ports import KeyProvider


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class HkdfSecretSigner:
    """Implements :class:`app.core.crypto.ports.SecretSigner`."""

    def __init__(self, provider: KeyProvider) -> None:
        self._provider = provider
        self._subkey_cache: dict[tuple[str, str], bytes] = {}

    def _subkey(self, purpose: str, kid: str) -> bytes | None:
        cache_key = (purpose, kid)
        cached = self._subkey_cache.get(cache_key)
        if cached is not None:
            return cached
        material = self._provider.signing_keyring().get(kid)
        if material is None:
            return None
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=f"lemma-sign:{purpose}".encode("utf-8"),
        ).derive(material.secret)
        self._subkey_cache[cache_key] = derived
        return derived

    def sign(self, purpose: str, payload: bytes) -> str:
        kid = self._provider.signing_keyring().primary_kid
        subkey = self._subkey(purpose, kid)
        if subkey is None:  # pragma: no cover - primary is always present
            raise RuntimeError(f"Primary signing key {kid!r} is unavailable")
        signature = hmac.new(subkey, payload, hashlib.sha256).digest()
        return f"{kid}.{_b64e(signature)}"

    def verify(self, purpose: str, payload: bytes, signature: str) -> bool:
        if "." not in signature:
            # Not a unified ("<kid>.<sig>") signature — caller handles legacy.
            return False
        kid, sig_b64 = signature.split(".", 1)
        subkey = self._subkey(purpose, kid)
        if subkey is None:
            return False
        expected = hmac.new(subkey, payload, hashlib.sha256).digest()
        try:
            return hmac.compare_digest(expected, _b64d(sig_b64))
        except ValueError, TypeError:
            return False
