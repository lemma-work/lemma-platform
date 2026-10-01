"""The process's keyring, and bringing it up.

API and worker processes load the keyring before they serve, which is when the
root key is exercised: a hosted process whose root is misconfigured (no KMS
permission, a malformed key name, no key at all) refuses to start instead of
failing on the first secret it touches. A database without the vault tables
yet -- a process started ahead of its migration -- is only warned about; the
keyring then loads on first use.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from sqlalchemy.exc import SQLAlchemyError

from app.core.crypto.roots.factory import (
    get_root_key_provider,
    validate_root_key_config,
)
from app.core.crypto.roots.ports import RootKeyError
from app.core.crypto.signing_keys import register_signing_keyring_source
from app.core.infrastructure.db.session import async_session_maker
from app.core.log.log import get_logger
from app.core.request_context import create_background_task

from app.modules.vault.config import vault_settings
from app.modules.vault.services.keyring import VaultKeyring

logger = get_logger(__name__)

_lock = threading.Lock()
_keyring: VaultKeyring | None = None


def get_vault_keyring() -> VaultKeyring:
    global _keyring
    if _keyring is None:
        with _lock:
            if _keyring is None:
                # Only the process's own keyring feeds the signer.
                _keyring = VaultKeyring(
                    get_root_key_provider(),
                    async_session_maker,
                    on_loaded=lambda keyring: register_signing_keyring_source(
                        keyring.signing_keyring
                    ),
                )
    return _keyring


def reset_vault_runtime() -> None:
    """Forget the keyring (tests; after changing root configuration)."""
    global _keyring
    with _lock:
        _keyring = None
    register_signing_keyring_source(None)


async def start_vault() -> None:
    """Validate the root and load keys. Raises :class:`RootKeyError` to refuse start."""
    validate_root_key_config()
    try:
        await get_vault_keyring().load()
    except SQLAlchemyError:
        logger.warning("vault.keyring.schema_missing.degraded", exc_info=True)


async def _refresh_forever() -> None:
    while True:
        await asyncio.sleep(vault_settings.vault_kek_refresh_seconds)
        try:
            await get_vault_keyring().load()
        except SQLAlchemyError, RootKeyError, OSError:
            # Keep the keys already loaded: they still decrypt everything they
            # did a minute ago. Only a rotation elsewhere goes unseen until the
            # next attempt succeeds.
            logger.warning("vault.keyring.refresh.degraded", exc_info=True)


@asynccontextmanager
async def running_vault() -> AsyncIterator[None]:
    """Start the vault for a process's lifetime: load keys, then keep them fresh."""
    await start_vault()
    task = create_background_task(_refresh_forever(), name="vault-keyring-refresh")
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
