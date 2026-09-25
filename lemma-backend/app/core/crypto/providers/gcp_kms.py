"""Google Cloud KMS key provider — envelope encryption.

The KEK lives in Cloud KMS and never leaves Google. Each secret is encrypted
locally with a fresh Fernet **data key (DEK)**; the DEK is wrapped by KMS
``Encrypt`` and stored next to the ciphertext, and recovered with KMS
``Decrypt``. Key rotation is KMS-native: a new KEK version becomes primary for
new wraps while old versions still unwrap existing DEKs, so the data ciphertext
never has to be rewritten (an optional rewrap-forward can re-wrap DEKs under the
newest version — see :mod:`app.core.crypto.rotation`).

Performance / caveats:
- The sync cipher interface means the KMS Encrypt/Decrypt calls block the event
  loop. Decrypted DEKs are cached by wrapped-bytes so repeated reads of the same
  row don't re-hit KMS; writes (infrequent — credential create/update) make one
  KMS Encrypt each. A fully non-blocking path would require an async cipher
  interface (future work).
- Signing must stay local (HMAC on hot paths can't call KMS per request), so
  :meth:`signing_keyring` uses a local key resolved from config — set
  ``SECRET_ENCRYPTION_KEY`` even when encrypting via KMS; it is used only as the
  HKDF root for token signing, never to encrypt credentials.
"""

from __future__ import annotations

import threading

from app.core.bounded import BoundedDict
from app.core.config import settings
from app.core.crypto.envelope import ALG_KMS_FERNET
from app.core.crypto.keys import load_static_keyring
from app.core.crypto.ports import KeyProvider, Keyring
from app.core.log.log import get_logger

logger = get_logger(__name__)


class GcpKmsKeyProvider(KeyProvider):
    name = "gcp_kms"

    def __init__(
        self,
        key_name: str | None = None,
        *,
        unwrap_cache_size: int = 2048,
    ) -> None:
        self._key_name = key_name or settings.gcp_kms_key_name
        if not self._key_name:
            raise RuntimeError(
                "GCP_KMS_KEY_NAME is required for secret_key_provider=gcp_kms "
                "(projects/…/locations/…/keyRings/…/cryptoKeys/…)"
            )
        self._client = None
        self._lock = threading.Lock()
        self._primary_kid: str | None = None
        self._signing_keyring: Keyring | None = None
        # read-path cache: wrapped DEK bytes -> plaintext DEK bytes
        self._unwrap_cache: BoundedDict[bytes, bytes] = BoundedDict(
            unwrap_cache_size, name="crypto.kms_unwrap_cache", touch_on_get=True
        )
        self._unwrap_cache_size = unwrap_cache_size

    # ------------------------------------------------------------------ client
    def _kms(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    from google.cloud import kms

                    self._client = kms.KeyManagementServiceClient()
        return self._client

    @staticmethod
    def _version_kid(version_resource: str) -> str:
        # ".../cryptoKeyVersions/3" -> "v3"
        return "v" + version_resource.rsplit("/", 1)[-1]

    # -------------------------------------------------------------- properties
    @property
    def encryption_alg(self) -> str:
        return ALG_KMS_FERNET

    @property
    def primary_kid(self) -> str:
        if self._primary_kid is None:
            with self._lock:
                if self._primary_kid is None:
                    crypto_key = self._kms().get_crypto_key(name=self._key_name)
                    self._primary_kid = self._version_kid(crypto_key.primary.name)
        return self._primary_kid

    # --------------------------------------------------------------- envelope
    def wrap_dek(self, dek: bytes) -> tuple[str, bytes]:
        response = self._kms().encrypt(
            request={"name": self._key_name, "plaintext": dek}
        )
        kid = self._version_kid(response.name)
        wrapped = response.ciphertext
        self._cache_unwrap(wrapped, dek)
        return kid, wrapped

    def unwrap_dek(self, kid: str, wrapped: bytes) -> bytes:
        cached = self._unwrap_cache.get(wrapped)
        if cached is not None:
            return cached
        response = self._kms().decrypt(
            request={"name": self._key_name, "ciphertext": wrapped}
        )
        dek = response.plaintext
        self._cache_unwrap(wrapped, dek)
        return dek

    def _cache_unwrap(self, wrapped: bytes, dek: bytes) -> None:
        self._unwrap_cache[wrapped] = dek

    # ---------------------------------------------------------------- signing
    def signing_keyring(self) -> Keyring:
        if self._signing_keyring is None:
            # Local HKDF root (set SECRET_ENCRYPTION_KEY); used only for signing.
            self._signing_keyring = load_static_keyring()
        return self._signing_keyring
