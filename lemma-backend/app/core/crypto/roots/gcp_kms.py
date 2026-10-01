"""Cloud KMS as the root key.

The KEK is wrapped by a Cloud KMS symmetric key and only ever unwrapped by
KMS, under the process's own service account. There is no key in the
environment: access is an IAM binding, and every unwrap is in Cloud Audit Logs.

KMS is called when a process starts (once per KEK) and when a KEK is created
or rotated -- never per secret. Reading a secret after start is local.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from app.core.concurrency.offload import run_blocking
from app.core.crypto.roots.kms_client import CloudKms, crypto_key_of
from app.core.crypto.roots.ports import RootKeyProvider, WrappedKey

REF_PREFIX = "gcp_kms:"


class GcpKmsRoot(RootKeyProvider):
    name = "gcp_kms"

    def __init__(
        self,
        key_name: str,
        *,
        key_version: str | None = None,
        client: Callable[[], CloudKms],
    ) -> None:
        self._key_name = key_name
        self._key_version = key_version
        self._client_factory = client
        self._client: CloudKms | None = None
        self._lock = threading.Lock()

    def _kms(self) -> CloudKms:
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = self._client_factory()
        return self._client

    async def wrap(self, key: bytes, *, aad: bytes) -> WrappedKey:
        target = self._key_version or self._key_name
        version, blob = await run_blocking(
            self._kms().encrypt, target, key, aad, limiter="crypto"
        )
        return WrappedKey(REF_PREFIX + version, blob)

    async def unwrap(self, root_ref: str, blob: bytes, *, aad: bytes) -> bytes:
        # The ref's own key, not the configured one: after an operator moves to
        # a new key, KEKs wrapped under the old one still open (given IAM on
        # it) until the start-up rewrap moves them.
        key = (
            root_ref.removeprefix(REF_PREFIX)
            if root_ref.startswith(REF_PREFIX)
            else self._key_name
        )
        return await run_blocking(self._kms().decrypt, key, blob, aad, limiter="crypto")

    def needs_rewrap(self, root_ref: str) -> bool:
        if not root_ref.startswith(REF_PREFIX):
            return True
        version = root_ref.removeprefix(REF_PREFIX)
        if self._key_version:
            return version != self._key_version
        return crypto_key_of(version) != self._key_name
