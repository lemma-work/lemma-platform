"""Choose the root key from configuration, and refuse a bad one at start.

``SECRET_KEY_PROVIDER`` selects it (``auto`` picks ``gcp_kms`` when
``GCP_KMS_KEY_NAME`` is set). Outside local mode the configuration is checked
when the process starts, not when the first secret is written: a hosted
process that cannot protect secrets should not come up at all.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Protocol

from app.core.config import settings
from app.core.crypto.config import crypto_settings
from app.core.crypto.keys import load_static_keyring
from app.core.crypto.roots.gcp_kms import GcpKmsRoot
from app.core.crypto.roots.kms_client import google_cloud_kms
from app.core.crypto.roots.local import (
    LocalKeyringRoot,
    load_keychain_keyring,
    load_secret_manager_keyring,
)
from app.core.crypto.roots.ports import RootKeyError, RootKeyProvider

_KMS_KEY = re.compile(
    r"^projects/[^/]+/locations/[^/]+/keyRings/[^/]+/cryptoKeys/[^/]+$"
)
_KMS_VERSION = re.compile(r"/cryptoKeyVersions/\d+$")


def create_root_key_provider(kind: str | None = None) -> RootKeyProvider:
    kind = kind or settings.effective_secret_key_provider()
    if kind == "gcp_kms":
        key_name = settings.gcp_kms_key_name or ""
        return GcpKmsRoot(
            key_name,
            key_version=crypto_settings.gcp_kms_key_version,
            client=lambda: google_cloud_kms(
                timeout_seconds=crypto_settings.gcp_kms_timeout_seconds,
                max_attempts=crypto_settings.gcp_kms_max_attempts,
            ),
        )
    if kind == "gcp_secret_manager":
        secret_name = settings.gcp_secret_manager_secret_name or ""
        return LocalKeyringRoot(
            "gcp_secret_manager", lambda: load_secret_manager_keyring(secret_name)
        )
    if kind == "keychain":
        return LocalKeyringRoot("keychain", load_keychain_keyring)
    return LocalKeyringRoot("static", load_static_keyring)


@lru_cache(maxsize=1)
def get_root_key_provider() -> RootKeyProvider:
    return create_root_key_provider()


def reset_root_key_provider() -> None:
    get_root_key_provider.cache_clear()


class _RootSettings(Protocol):
    gcp_kms_key_name: str | None
    gcp_secret_manager_secret_name: str | None

    def effective_secret_key_provider(self) -> str: ...

    def is_local_mode(self) -> bool: ...


class _KmsVersionSettings(Protocol):
    gcp_kms_key_version: str | None


def validate_root_key_config(
    config: _RootSettings | None = None, crypto: _KmsVersionSettings | None = None
) -> None:
    """Raise :class:`RootKeyError` for a root that cannot work where we run."""
    config = config or settings
    crypto = crypto or crypto_settings
    kind = config.effective_secret_key_provider()
    hosted = not config.is_local_mode()
    if kind == "gcp_kms":
        key_name = config.gcp_kms_key_name or ""
        if not _KMS_KEY.match(key_name):
            raise RootKeyError(
                "GCP_KMS_KEY_NAME must be projects/<p>/locations/<l>/keyRings/<r>/"
                "cryptoKeys/<k>"
            )
        version = crypto.gcp_kms_key_version
        if version and not (
            version.startswith(key_name + "/") and _KMS_VERSION.search(version)
        ):
            raise RootKeyError(
                "GCP_KMS_KEY_VERSION must be a cryptoKeyVersions/<n> of GCP_KMS_KEY_NAME"
            )
        return
    if kind == "gcp_secret_manager":
        if not config.gcp_secret_manager_secret_name:
            raise RootKeyError(
                "GCP_SECRET_MANAGER_SECRET_NAME is required for "
                "secret_key_provider=gcp_secret_manager"
            )
        return
    if kind == "keychain":
        if hosted:
            raise RootKeyError(
                "secret_key_provider=keychain is for a backend run from a checkout; "
                "use gcp_kms or static outside local mode"
            )
        return
    if hosted:
        # Raises for a missing or malformed key. Local mode may fall back to the
        # published development key, and says so when it does.
        try:
            load_static_keyring()
        except RuntimeError as exc:
            raise RootKeyError(str(exc)) from exc
