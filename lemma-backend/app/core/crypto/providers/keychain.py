"""OS keychain key provider — local developer convenience.

Stores the Fernet keyring in the operating-system keychain (via the ``keyring``
library) instead of an ``.env`` file, so a developer's encryption key isn't
sitting in plaintext on disk. On first use with no stored keyring, a fresh
single-key keyset is generated and saved. Crypto is local (like the static
provider).

Not intended for hosted environments — use gcp_kms / gcp_secret_manager there.
"""

from __future__ import annotations

import json
import threading

from cryptography.fernet import Fernet

from app.core.crypto.envelope import ALG_FERNET
from app.core.crypto.keys import parse_keyset
from app.core.crypto.ports import KeyProvider, Keyring

KEYCHAIN_SERVICE = "lemma-backend"
KEYCHAIN_USERNAME = "secret-encryption-keyset"


class KeychainKeyProvider(KeyProvider):
    name = "keychain"

    def __init__(
        self,
        service: str = KEYCHAIN_SERVICE,
        username: str = KEYCHAIN_USERNAME,
    ) -> None:
        self._service = service
        self._username = username
        self._lock = threading.Lock()
        self._keyring: Keyring | None = None

    def _load(self) -> Keyring:
        if self._keyring is None:
            with self._lock:
                if self._keyring is None:
                    import keyring as kc

                    raw = kc.get_password(self._service, self._username)
                    if not raw:
                        raw = json.dumps(
                            [
                                {
                                    "kid": "kc1",
                                    "key": Fernet.generate_key().decode(),
                                    "primary": True,
                                }
                            ]
                        )
                        kc.set_password(self._service, self._username, raw)
                    self._keyring = parse_keyset(raw)
        return self._keyring

    @property
    def encryption_alg(self) -> str:
        return ALG_FERNET

    @property
    def primary_kid(self) -> str:
        return self._load().primary_kid

    def encryption_keyring(self) -> Keyring:
        return self._load()

    def signing_keyring(self) -> Keyring:
        return self._load()
