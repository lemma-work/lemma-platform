"""The system-wide secret cipher.

``EnvelopeSecretCipher`` encrypts/decrypts secrets at rest using whatever
:class:`KeyProvider` is configured, emitting versioned, key-id-tagged envelopes
(:mod:`app.core.crypto.envelope`) and transparently reading legacy
``fernet-json-v1`` blobs.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.crypto import envelope as env
from app.core.crypto.ports import KeyProvider


class EnvelopeSecretCipher:
    """Implements :class:`app.core.crypto.ports.SecretCipher`."""

    def __init__(
        self,
        provider: KeyProvider,
        legacy_secrets: list[bytes] | None = None,
    ) -> None:
        self._provider = provider
        self._fernet_cache: dict[str, Fernet] = {}
        # MultiFernet over every candidate key, used only to read legacy v1 blobs
        # (and to read v2/fernet blobs whose kid is unknown to the active keyring).
        self._legacy: MultiFernet | None = None
        if legacy_secrets:
            self._legacy = MultiFernet([Fernet(secret) for secret in legacy_secrets])

    # ----------------------------------------------------------------- helpers
    def _fernet_for_kid(self, kid: str) -> Fernet | None:
        cached = self._fernet_cache.get(kid)
        if cached is not None:
            return cached
        keyring = self._provider.encryption_keyring()
        material = keyring.get(kid) if keyring else None
        if material is None:
            return None
        fernet = Fernet(material.secret)
        self._fernet_cache[kid] = fernet
        return fernet

    def _encrypt_payload(self, payload: bytes) -> dict[str, Any]:
        if self._provider.encryption_alg == env.ALG_KMS_FERNET:
            dek = Fernet.generate_key()
            kek_kid, wrapped = self._provider.wrap_dek(dek)
            ciphertext = Fernet(dek).encrypt(payload)
            return env.make_v2(
                kid=kek_kid,
                alg=env.ALG_KMS_FERNET,
                ciphertext=ciphertext,
                wrapped_dek=wrapped,
            )
        # local fernet keyring
        kid = self._provider.primary_kid
        fernet = self._fernet_for_kid(kid)
        if fernet is None:
            raise RuntimeError(f"Primary key {kid!r} is not available in the keyring")
        return env.make_v2(kid=kid, alg=env.ALG_FERNET, ciphertext=fernet.encrypt(payload))

    def _decrypt_v2(self, envelope: dict[str, Any]) -> bytes:
        alg = envelope.get("alg")
        ciphertext = env.b64d(envelope["ct"])
        if alg == env.ALG_KMS_FERNET:
            dek = self._provider.unwrap_dek(
                str(envelope["kid"]), env.b64d(envelope["dek"])
            )
            return Fernet(dek).decrypt(ciphertext)
        if alg == env.ALG_FERNET:
            fernet = self._fernet_for_kid(str(envelope["kid"]))
            if fernet is not None:
                return fernet.decrypt(ciphertext)
            if self._legacy is not None:
                return self._legacy.decrypt(ciphertext)
            raise RuntimeError(
                f"No key available to decrypt envelope (kid={envelope.get('kid')!r})"
            )
        raise ValueError(f"Unknown envelope alg: {alg!r}")

    def _decrypt_v1(self, envelope: dict[str, Any]) -> bytes:
        token = envelope.get("ciphertext")
        if not isinstance(token, str):
            raise ValueError("Encrypted JSON payload is missing ciphertext")
        if self._legacy is None:
            raise RuntimeError(
                "Cannot decrypt legacy fernet-json-v1 data: no legacy key configured"
            )
        return self._legacy.decrypt(token.encode("ascii"))

    # -------------------------------------------------------------- json (dict)
    def encrypt_json(self, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None or env.is_encrypted_dict(value):
            return value
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return self._encrypt_payload(payload)

    def decrypt_json(self, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None or not env.is_encrypted_dict(value):
            return value
        if env.is_v2(value):
            payload = self._decrypt_v2(value)
        else:
            payload = self._decrypt_v1(value)
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Encrypted JSON payload did not decode to an object")
        return decoded

    # Async variants offload the crypto off the event loop — with a KMS-backed
    # KeyProvider, encrypt/decrypt makes a blocking gRPC round-trip (wrap/unwrap
    # DEK) that must not run on the loop. Callers on the worker loop (e.g. the
    # connector credential repositories) use these instead of the sync methods.
    async def encrypt_json_async(
        self, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        from app.core.concurrency.offload import run_blocking

        return await run_blocking(self.encrypt_json, value, limiter="crypto")

    async def decrypt_json_async(
        self, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        from app.core.concurrency.offload import run_blocking

        return await run_blocking(self.decrypt_json, value, limiter="crypto")

    # ------------------------------------------------------------ string column
    def encrypt_str(self, value: str | None) -> str | None:
        if value is None or env.is_encrypted_str(value):
            return value
        envelope = self._encrypt_payload(value.encode("utf-8"))
        return env.encode_str(envelope)

    def decrypt_str(self, value: str | None) -> str | None:
        if value is None or not env.is_encrypted_str(value):
            return value
        payload = self._decrypt_v2(env.decode_str(value))
        return payload.decode("utf-8")

    # -------------------------------------------------------------- rotation aid
    def is_under_primary_key(self, value: Any) -> bool:
        """True when ``value`` is already encrypted under the current primary key.

        Plaintext, legacy v1, and ciphertext under a retired kid all return
        False so the rotation walker re-encrypts them forward.
        """
        envelope: dict[str, Any] | None = None
        if env.is_v2(value):
            envelope = value
        elif env.is_encrypted_str(value):
            try:
                envelope = env.decode_str(value)
            except (ValueError, json.JSONDecodeError):
                return False
        if envelope is None:
            return False
        return (
            envelope.get("alg") == self._provider.encryption_alg
            and str(envelope.get("kid")) == self._provider.primary_kid
        )


__all__ = ["EnvelopeSecretCipher", "InvalidToken"]
