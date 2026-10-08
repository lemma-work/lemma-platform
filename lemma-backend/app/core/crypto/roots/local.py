"""A root held as a keyset: static config, the OS keychain, or Secret Manager.

The three differ only in where the keyset comes from; the crypto is the same.
Each keyset entry's key material is stretched with HKDF into an AES-256 root
key, so the keyset format (the Fernet keys operators already have) is kept.

Rotation needs no job: add a new primary entry to the keyset and restart. The
vault sees that its KEKs were wrapped under a key that is no longer primary
(:meth:`needs_rewrap`) and rewraps them at start -- a few local operations.
Keep the old entry until that has happened on every process.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.crypto.aead import InvalidTag, open_sealed, seal
from app.core.crypto.keys import parse_keyset
from app.core.crypto.ports import Keyring
from app.core.crypto.roots.ports import RootKeyError, RootKeyProvider, WrappedKey

REF_PREFIX = "local:"
KEYCHAIN_SERVICE = "lemma-backend"
KEYCHAIN_USERNAME = "secret-encryption-keyset"
_ROOT_INFO = b"lemma-vault-root/v1"


class LocalKeyringRoot(RootKeyProvider):
    def __init__(self, name: str, load: Callable[[], Keyring]) -> None:
        self.name = name
        self._load = load
        self._lock = threading.Lock()
        self._keyring: Keyring | None = None

    def keyring(self) -> Keyring:
        if self._keyring is None:
            with self._lock:
                if self._keyring is None:
                    self._keyring = self._load()
        return self._keyring

    def _root_key(self, kid: str) -> bytes:
        material = self.keyring().get(kid)
        if material is None:
            raise RootKeyError(
                f"Root keyset has no key {kid!r}. A KEK was wrapped under a key "
                "that has since been removed from SECRET_ENCRYPTION_KEYSET; put "
                "it back until the vault has rewrapped its KEKs."
            )
        return HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=_ROOT_INFO
        ).derive(material.secret)

    async def wrap(self, key: bytes, *, aad: bytes) -> WrappedKey:
        kid = self.keyring().primary_kid
        return WrappedKey(REF_PREFIX + kid, seal(self._root_key(kid), key, aad))

    async def unwrap(self, root_ref: str, blob: bytes, *, aad: bytes) -> bytes:
        if not root_ref.startswith(REF_PREFIX):
            raise RootKeyError(
                f"KEK was wrapped by root {root_ref.split(':', 1)[0]!r}, but this "
                f"process is configured with {self.name!r}"
            )
        try:
            return open_sealed(self._root_key(root_ref[len(REF_PREFIX) :]), blob, aad)
        except InvalidTag as exc:
            raise RootKeyError(
                f"Root key {root_ref!r} did not open its KEK: the keyset entry "
                "with that id is not the key that wrapped it"
            ) from exc

    def needs_rewrap(self, root_ref: str) -> bool:
        return root_ref != REF_PREFIX + self.keyring().primary_kid


def load_keychain_keyring(
    service: str = KEYCHAIN_SERVICE, username: str = KEYCHAIN_USERNAME
) -> Keyring:
    """The keyset in the OS keychain, created on first use.

    For a backend run from a checkout. Packaged Desktop does not use this: its
    daemon keeps the keyset in the OS vault and passes it in as a static keyset.
    """
    import keyring as kc

    raw = kc.get_password(service, username)
    if not raw:
        raw = json.dumps(
            [{"kid": "kc1", "key": Fernet.generate_key().decode(), "primary": True}]
        )
        kc.set_password(service, username, raw)
    return parse_keyset(raw)


def load_secret_manager_keyring(secret_name: str) -> Keyring:
    """The keyset stored as a Google Secret Manager secret (ADC credentials)."""
    try:
        from google.cloud import secretmanager
    except ImportError as exc:
        raise RootKeyError(
            "secret_key_provider=gcp_secret_manager needs the gcp-kms extra "
            "(google-cloud-secret-manager) installed"
        ) from exc
    name = (
        secret_name if "/versions/" in secret_name else f"{secret_name}/versions/latest"
    )
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(request={"name": name})
    return parse_keyset(response.payload.data.decode("utf-8"))
