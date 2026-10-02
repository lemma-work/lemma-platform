"""The key-encryption keys, unwrapped once and held in memory.

Reading a secret needs its KEK. KEKs are few (one active per purpose, plus any
kept for decryption after a rotation), so every process unwraps all of them
when it starts and keeps them -- the only in-process cache the vault has, and
the only use of the root key after start. Plaintext secrets are never cached.

Loading reads ``vault_keys`` on a short session of its own and closes it before
unwrapping: with Cloud KMS the unwrap is a network call, and no caller's
connection is held across it.

Two purposes: ``encrypt`` (wraps data keys) and ``sign`` (the HMAC root for
token signing when the root key is remote). A missing active key is created on
first load; concurrent creators race on a partial unique index and the loser
reloads the winner's.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid7

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto.aad import encode_aad
from app.core.crypto.aead import new_key
from app.core.crypto.ports import KeyMaterial, Keyring
from app.core.crypto.roots.ports import RootKeyProvider
from app.core.crypto.signing_keys import local_signing_keyring
from app.core.log.log import get_logger

from app.modules.vault.domain.errors import VaultUnavailable
from app.modules.vault.infrastructure.models import VaultKey

logger = get_logger(__name__)

ENCRYPT = "encrypt"
SIGN = "sign"
ACTIVE = "active"
DECRYPT_ONLY = "decrypt_only"
RETIRED = "retired"
_MAX_LIVE_KEYS = 1000


def kek_aad(key_id: UUID, purpose: str) -> bytes:
    return encode_aad("lemma-vault/kek/v1", str(key_id), purpose)


def signing_kid(key_id: UUID) -> str:
    return "v" + key_id.hex[:12]


@dataclass(frozen=True, slots=True)
class _Key:
    id: UUID
    purpose: str
    state: str
    material: bytes


class VaultKeyring:
    def __init__(
        self,
        root: RootKeyProvider,
        session_factory: Callable[[], AsyncSession],
        *,
        on_loaded: Callable[[VaultKeyring], None] | None = None,
    ) -> None:
        self._root = root
        self._session_factory = session_factory
        self._on_loaded = on_loaded
        self._lock = threading.Lock()
        self._keys: dict[UUID, _Key] = {}
        self._active: dict[str, UUID] = {}
        self._signing: Keyring | None = None
        self._loaded = False

    @property
    def root(self) -> RootKeyProvider:
        return self._root

    @property
    def loaded(self) -> bool:
        return self._loaded

    # ------------------------------------------------------------------ reads
    def cached_active_encryption_key(self) -> tuple[UUID, bytes] | None:
        """The active KEK if loaded. No I/O: the path every secret write takes."""
        key_id = self._active.get(ENCRYPT)
        return None if key_id is None else (key_id, self._keys[key_id].material)

    def cached_encryption_key(self, key_id: UUID) -> bytes | None:
        """A KEK if loaded. No I/O: the path every secret read takes."""
        key = self._keys.get(key_id)
        return None if key is None else key.material

    async def active_encryption_key(self) -> tuple[UUID, bytes]:
        if ENCRYPT not in self._active:
            await self.load()
        key_id = self._active.get(ENCRYPT)
        if key_id is None:
            raise VaultUnavailable("no active vault encryption key")
        return key_id, self._keys[key_id].material

    async def encryption_key(self, key_id: UUID) -> bytes:
        key = self._keys.get(key_id)
        if key is None:
            # Created or re-activated by another process since we loaded.
            await self.load()
            key = self._keys.get(key_id)
        if key is None:
            raise VaultUnavailable(f"vault key {key_id} is retired or missing")
        return key.material

    def signing_keyring(self) -> Keyring:
        if self._signing is None:
            raise VaultUnavailable("vault signing keys are not loaded")
        return self._signing

    # ---------------------------------------------------------------- loading
    async def load(self) -> None:
        """(Re)read every usable key; create missing active ones; rewrap stale."""
        rows = await self._read_keys()
        for purpose in (ENCRYPT, SIGN):
            if not any(r.purpose == purpose and r.state == ACTIVE for r in rows):
                await self._bootstrap(purpose)
                rows = await self._read_keys()
        keys: dict[UUID, _Key] = {}
        active: dict[str, UUID] = {}
        stale: list[tuple[UUID, str, bytes]] = []
        for row in rows:
            aad = kek_aad(row.id, row.purpose)
            material = await self._root.unwrap(
                row.root_key_ref, row.wrapped_key, aad=aad
            )
            keys[row.id] = _Key(row.id, row.purpose, row.state, material)
            if row.state == ACTIVE:
                active[row.purpose] = row.id
            if self._root.needs_rewrap(row.root_key_ref):
                stale.append((row.id, row.purpose, material))
        for key_id, purpose, material in stale:
            await self._rewrap(key_id, purpose, material)
        signing = _signing_keyring(keys, active, local_signing_keyring())
        with self._lock:
            self._keys = keys
            self._active = active
            self._signing = signing
            self._loaded = True
        if self._on_loaded is not None:
            self._on_loaded(self)
        logger.info(
            "vault.keyring.loaded",
            root_provider=self._root.name,
            key_count=len(keys),
            rewrapped_count=len(stale),
        )

    async def _read_keys(self) -> list[VaultKey]:
        async with self._session_factory() as session:
            # Bounded by how often anyone rotates: one active key per purpose
            # plus those kept for decryption. The cap is a backstop, not a page.
            result = await session.execute(
                select(VaultKey)
                .where(VaultKey.state != RETIRED)
                .order_by(VaultKey.created_at.desc())
                .limit(_MAX_LIVE_KEYS)
            )
            return list(result.scalars().all())

    async def _bootstrap(self, purpose: str) -> None:
        key_id = uuid7()
        wrapped = await self._root.wrap(new_key(), aad=kek_aad(key_id, purpose))
        async with self._session_factory() as session:
            await session.execute(
                insert(VaultKey)
                .values(
                    id=key_id,
                    purpose=purpose,
                    state=ACTIVE,
                    root_provider=self._root.name,
                    root_key_ref=wrapped.root_ref,
                    wrapped_key=wrapped.blob,
                )
                # A literal predicate: Postgres infers the partial index from it,
                # and cannot from a bound parameter.
                .on_conflict_do_nothing(
                    index_elements=["purpose"], index_where=text("state = 'active'")
                )
            )
            await session.commit()
        logger.info("vault.keyring.key_created", purpose=purpose, key_id=str(key_id))

    async def _rewrap(self, key_id: UUID, purpose: str, material: bytes) -> None:
        wrapped = await self._root.wrap(material, aad=kek_aad(key_id, purpose))
        async with self._session_factory() as session:
            await session.execute(
                update(VaultKey)
                .where(VaultKey.id == key_id)
                .values(
                    root_provider=self._root.name,
                    root_key_ref=wrapped.root_ref,
                    wrapped_key=wrapped.blob,
                )
            )
            await session.commit()

    # --------------------------------------------------------------- rotation
    async def rotate(self, purpose: str) -> UUID:
        """Make a new active key; the old one keeps decrypting (or verifying).

        Other processes pick the new key up on their next refresh; until then
        they keep writing under the old one, which stays readable -- so there
        is no window in which anything fails.
        """
        key_id = uuid7()
        wrapped = await self._root.wrap(new_key(), aad=kek_aad(key_id, purpose))
        async with self._session_factory() as session:
            await session.execute(
                update(VaultKey)
                .where(VaultKey.purpose == purpose, VaultKey.state == ACTIVE)
                .values(state=DECRYPT_ONLY)
            )
            session.add(
                VaultKey(
                    id=key_id,
                    purpose=purpose,
                    state=ACTIVE,
                    root_provider=self._root.name,
                    root_key_ref=wrapped.root_ref,
                    wrapped_key=wrapped.blob,
                )
            )
            await session.commit()
        await self.load()
        return key_id

    async def retire(self, key_id: UUID) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(VaultKey)
                .where(VaultKey.id == key_id, VaultKey.state == DECRYPT_ONLY)
                .values(state=RETIRED, retired_at=datetime.now(timezone.utc))
            )
            await session.commit()
        await self.load()


def _signing_keyring(
    keys: dict[UUID, _Key], active: dict[str, UUID], local: Keyring | None
) -> Keyring:
    """Vault signing keys, plus the local keyset when there is one.

    The local keyset stays primary when present, so a deployment that already
    signs with ``SECRET_ENCRYPTION_KEY`` keeps minting the same tokens; the
    vault key becomes primary only where there is no local key (Cloud KMS).
    Tokens signed under either keep verifying.
    """
    materials = {
        signing_kid(key.id): KeyMaterial(signing_kid(key.id), key.material)
        for key in keys.values()
        if key.purpose == SIGN
    }
    if local is not None:
        materials.update(local.keys)
        return Keyring(primary_kid=local.primary_kid, keys=materials)
    return Keyring(primary_kid=signing_kid(active[SIGN]), keys=materials)
