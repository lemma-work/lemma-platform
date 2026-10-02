"""Cloud KMS Encrypt/Decrypt with the checks Google asks callers to make.

Three things the previous provider did not do, all of which matter once KMS is
the root of every secret:

- **Integrity.** Every request carries a CRC32C of its inputs and every
  response's is checked, so a byte flipped on the way to or from the HSM is an
  error instead of a key that silently decrypts nothing.
- **Associated data.** The wrap is bound to what is being wrapped, so a wrapped
  KEK cannot be passed off as a different one.
- **Deadlines and retries.** A timeout per attempt and retries on the transient
  statuses only; a permission or not-found error fails at once with what to fix.

Credentials come from Application Default Credentials -- Workload Identity or
the service account the process runs as. No key material is configured.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from typing import Protocol

from app.core.crypto.crc32c import crc32c
from app.core.crypto.roots.ports import RootKeyError

_VERSION_SUFFIX = re.compile(r"/cryptoKeyVersions/[^/]+$")


class _EncryptResult(Protocol):
    name: str
    ciphertext: bytes
    ciphertext_crc32c: int
    verified_plaintext_crc32c: bool
    verified_additional_authenticated_data_crc32c: bool


class _DecryptResult(Protocol):
    plaintext: bytes
    plaintext_crc32c: int


class KmsApi(Protocol):
    """The two calls used from ``google.cloud.kms.KeyManagementServiceClient``."""

    def encrypt(
        self, *, request: Mapping[str, object], retry: object, timeout: float
    ) -> _EncryptResult: ...

    def decrypt(
        self, *, request: Mapping[str, object], retry: object, timeout: float
    ) -> _DecryptResult: ...


def crypto_key_of(resource: str) -> str:
    """``…/cryptoKeys/k/cryptoKeyVersions/3`` -> ``…/cryptoKeys/k``."""
    return _VERSION_SUFFIX.sub("", resource)


class CloudKms:
    """Synchronous; callers run it off the event loop."""

    def __init__(
        self,
        api: KmsApi,
        *,
        retry: object,
        error_types: tuple[type[BaseException], ...],
        timeout_seconds: float,
    ) -> None:
        self._api = api
        self._retry = retry
        self._error_types = error_types
        self._timeout = timeout_seconds

    def encrypt(self, name: str, plaintext: bytes, aad: bytes) -> tuple[str, bytes]:
        """Wrap ``plaintext`` with ``name`` (a key or a key version).

        Returns the version that actually wrapped it, and the ciphertext.
        """
        request: dict[str, object] = {
            "name": name,
            "plaintext": plaintext,
            "plaintext_crc32c": crc32c(plaintext),
        }
        if aad:
            request["additional_authenticated_data"] = aad
            request["additional_authenticated_data_crc32c"] = crc32c(aad)
        try:
            response = self._api.encrypt(
                request=request, retry=self._retry, timeout=self._timeout
            )
        except self._error_types as exc:
            raise _explain(exc, "encrypt", name) from exc
        if not response.verified_plaintext_crc32c or (
            aad and not response.verified_additional_authenticated_data_crc32c
        ):
            raise RootKeyError(
                "Cloud KMS did not verify the request checksum on encrypt"
            )
        if crc32c(response.ciphertext) != int(response.ciphertext_crc32c):
            raise RootKeyError("Cloud KMS encrypt response failed its checksum")
        return response.name, response.ciphertext

    def decrypt(self, key_name: str, ciphertext: bytes, aad: bytes) -> bytes:
        """Unwrap with ``key_name`` (a crypto key; KMS picks the version)."""
        request: dict[str, object] = {
            "name": crypto_key_of(key_name),
            "ciphertext": ciphertext,
            "ciphertext_crc32c": crc32c(ciphertext),
        }
        if aad:
            request["additional_authenticated_data"] = aad
            request["additional_authenticated_data_crc32c"] = crc32c(aad)
        try:
            response = self._api.decrypt(
                request=request, retry=self._retry, timeout=self._timeout
            )
        except self._error_types as exc:
            raise _explain(exc, "decrypt", key_name) from exc
        if crc32c(response.plaintext) != int(response.plaintext_crc32c):
            raise RootKeyError("Cloud KMS decrypt response failed its checksum")
        return response.plaintext


def _explain(exc: BaseException, operation: str, name: str) -> RootKeyError:
    kind = type(exc).__name__
    if kind in {"PermissionDenied", "Unauthenticated", "Forbidden"}:
        return RootKeyError(
            f"Cloud KMS refused {operation} on {crypto_key_of(name)} ({kind}). Grant "
            "roles/cloudkms.cryptoKeyEncrypterDecrypter on that key to the service "
            "account this process runs as."
        )
    if kind == "NotFound":
        return RootKeyError(
            f"Cloud KMS key {name} does not exist or is not visible to this "
            "service account; check GCP_KMS_KEY_NAME / GCP_KMS_KEY_VERSION."
        )
    if kind == "FailedPrecondition":
        return RootKeyError(
            f"Cloud KMS key {name} cannot {operation} (FailedPrecondition): the "
            "key version is disabled, destroyed, or not a symmetric key."
        )
    return RootKeyError(
        f"Cloud KMS {operation} on {crypto_key_of(name)} failed ({kind})"
    )


_client_lock = threading.Lock()


def google_cloud_kms(*, timeout_seconds: float, max_attempts: int) -> CloudKms:
    """The real client, over Application Default Credentials."""
    with _client_lock:
        try:
            from google.api_core import exceptions as gexc
            from google.api_core import retry as gretry
            from google.cloud import kms
        except ImportError as exc:
            raise RootKeyError(
                "secret_key_provider=gcp_kms needs the gcp-kms extra "
                "(google-cloud-kms) installed; the backend image installs it"
            ) from exc
        retry = gretry.Retry(
            initial=0.25,
            maximum=4.0,
            multiplier=2.0,
            timeout=timeout_seconds * max_attempts,
            predicate=gretry.if_exception_type(
                gexc.ServiceUnavailable,
                gexc.DeadlineExceeded,
                gexc.InternalServerError,
                gexc.TooManyRequests,
            ),
        )
        return CloudKms(
            kms.KeyManagementServiceClient(),
            retry=retry,
            error_types=(gexc.GoogleAPICallError, gexc.RetryError),
            timeout_seconds=timeout_seconds,
        )
