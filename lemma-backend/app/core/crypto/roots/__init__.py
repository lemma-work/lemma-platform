"""Root key providers: what protects the vault's key-encryption keys."""

from app.core.crypto.roots.factory import (
    create_root_key_provider,
    get_root_key_provider,
    reset_root_key_provider,
    validate_root_key_config,
)
from app.core.crypto.roots.ports import RootKeyError, RootKeyProvider, WrappedKey

__all__ = [
    "RootKeyError",
    "RootKeyProvider",
    "WrappedKey",
    "create_root_key_provider",
    "get_root_key_provider",
    "reset_root_key_provider",
    "validate_root_key_config",
]
