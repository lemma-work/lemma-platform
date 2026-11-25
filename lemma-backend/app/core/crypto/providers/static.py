"""Static key provider — keyring of raw Fernet keys from configuration.

The default backend for OSS and local stacks: keys live in env/config, crypto
happens in-process. Kept only to read secrets the previous release wrote in
their old columns; nothing new is encrypted here (see ``app.modules.vault``).
"""

from __future__ import annotations

from app.core.crypto.envelope import ALG_FERNET
from app.core.crypto.keys import load_static_keyring
from app.core.crypto.ports import KeyProvider, Keyring


class StaticKeyProvider(KeyProvider):
    name = "static"

    def __init__(self, keyring: Keyring | None = None) -> None:
        self._keyring = keyring or load_static_keyring()

    @property
    def encryption_alg(self) -> str:
        return ALG_FERNET

    @property
    def primary_kid(self) -> str:
        return self._keyring.primary_kid

    def encryption_keyring(self) -> Keyring:
        return self._keyring

    def signing_keyring(self) -> Keyring:
        # The same raw keys feed HKDF for HMAC signing.
        return self._keyring
