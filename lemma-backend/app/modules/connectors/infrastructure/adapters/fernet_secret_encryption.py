"""Deprecated shim — use ``app.core.crypto.get_secret_cipher()``.

The real implementation is now the system-wide ``EnvelopeSecretCipher`` in
``app/core/crypto``, which adds key-id versioning and rotation. New writes emit
``lemma-secret-v2`` envelopes; legacy ``fernet-json-v1`` data still decrypts.

This shim is kept only so any out-of-tree imports keep working; prefer
``get_secret_cipher()`` directly.
"""

from __future__ import annotations

from typing import Any

from app.core.concurrency.offload import run_blocking
from app.core.crypto.envelope import V1_MARKER
from app.core.crypto.factory import get_secret_cipher
from app.modules.connectors.domain.ports import SecretEncryptionPort

# Legacy marker, retained for backward-compatible reads/inspection only.
ENCRYPTED_JSON_MARKER = V1_MARKER


class FernetSecretEncryptionAdapter(SecretEncryptionPort):
    """Deprecated: delegates to the shared crypto facility."""

    def __init__(self, key: str | None = None):
        # ``key`` is ignored; key material now comes from the configured provider.
        self._cipher = get_secret_cipher()

    def encrypt_json(self, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return self._cipher.encrypt_json(value)

    def decrypt_json(self, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return self._cipher.decrypt_json(value)

    async def encrypt_json_async(
        self, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        # Offload onto the crypto limiter: with a KMS-backed provider this wraps
        # a blocking gRPC round-trip that must not run on the event loop. With
        # the static/keychain providers it's a cheap Fernet op — a negligible
        # thread hop.
        return await run_blocking(self.encrypt_json, value, limiter="crypto")

    async def decrypt_json_async(
        self, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return await run_blocking(self.decrypt_json, value, limiter="crypto")
