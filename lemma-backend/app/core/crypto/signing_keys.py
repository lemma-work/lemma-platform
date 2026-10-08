"""Where the signer gets its keys.

Signing is HMAC on hot request paths, so its keys must be in memory. With a
local root (a static keyset, the keychain, Secret Manager) the keyset that
roots the vault also signs, as it always has. With Cloud KMS there is no local
key to sign with -- that is the point of KMS -- so the vault holds a signing
key of its own, wrapped by the root, and registers it here once it has loaded.

The registration is an inversion rather than an import: core does not know the
vault module exists. Until something registers, the local keyring is used.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.crypto.keys import load_static_keyring
from app.core.crypto.ports import Keyring
from app.core.crypto.roots.factory import get_root_key_provider
from app.core.crypto.roots.local import LocalKeyringRoot

_source: Callable[[], Keyring] | None = None


def register_signing_keyring_source(source: Callable[[], Keyring] | None) -> None:
    """Install (or, with ``None``, remove) the keyring the signer reads."""
    global _source
    _source = source


def local_signing_keyring() -> Keyring | None:
    """The configured local keyset, or ``None`` when there is none to use.

    ``None`` only under a remote root with no ``SECRET_ENCRYPTION_KEY`` set,
    which is the configuration a KMS deployment is expected to reach.
    """
    root = get_root_key_provider()
    if isinstance(root, LocalKeyringRoot):
        return root.keyring()
    try:
        return load_static_keyring()
    except RuntimeError:
        return None


def signing_keyring() -> Keyring:
    if _source is not None:
        return _source()
    keyring = local_signing_keyring()
    if keyring is None:
        raise RuntimeError(
            "No signing key: the vault has not loaded yet and no "
            "SECRET_ENCRYPTION_KEY is configured"
        )
    return keyring
