"""Reading and writing secrets, on the caller's transaction.

``SqlVault`` never commits. A secret is written in the same transaction as the
row that points at it, so the two land together or not at all, and its audit
event with them.

Cost of a read: one primary-key lookup and two AES-GCM operations on keys
already in memory -- a few microseconds of CPU. Nothing here calls the root
key or leaves the process, so nothing is offloaded to a thread.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto.aead import InvalidTag
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger

from app.modules.vault.domain.errors import (
    VaultUnavailable,
    SecretIntegrityError,
    SecretNotFound,
    SecretScopeMismatch,
    SecretVersionConflict,
)
from app.modules.vault.domain.types import (
    AuditActor,
    JsonObject,
    LeaseToken,
    Revealed,
    SecretKind,
    SecretMeta,
    SecretRef,
    SecretScope,
    SecretValue,
    encode_value,
)
from app.modules.vault.domain.ports import KEEP, KeepExpiry
from app.modules.vault.infrastructure.models import VaultSecret
from app.modules.vault.services.audit import SecretAction, record_event
from app.modules.vault.services.envelope import ALG, open_value, seal_value
from app.modules.vault.services.keyring import VaultKeyring
from app.modules.vault.services.runtime import get_vault_keyring

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Stored:
    """A secret row as plain values.

    Columns rather than an ORM instance: a key load may hand the connection
    back mid-read (see ``_load_keys``), and a session that expires on commit
    would then lazy-load a tracked row outside any greenlet.
    """

    id: UUID
    organization_id: UUID | None
    pod_id: UUID | None
    user_id: UUID | None
    purpose: str
    kind: str
    version: int
    kek_id: UUID
    wrapped_dek: bytes
    ciphertext: bytes
    expires_at: datetime | None
    lease_holder: str | None
    lease_until: datetime | None
    updated_at: datetime


_COLUMNS = (
    VaultSecret.id,
    VaultSecret.organization_id,
    VaultSecret.pod_id,
    VaultSecret.user_id,
    VaultSecret.purpose,
    VaultSecret.kind,
    VaultSecret.version,
    VaultSecret.kek_id,
    VaultSecret.wrapped_dek,
    VaultSecret.ciphertext,
    VaultSecret.expires_at,
    VaultSecret.lease_holder,
    VaultSecret.lease_until,
    VaultSecret.updated_at,
)


class SqlVault:
    def __init__(
        self, session: AsyncSession, keyring: VaultKeyring | None = None
    ) -> None:
        self._session = session
        self._keyring = keyring

    @property
    def keyring(self) -> VaultKeyring:
        return self._keyring or get_vault_keyring()

    # ------------------------------------------------------------------ write
    async def put(
        self,
        *,
        scope: SecretScope,
        purpose: str,
        value: SecretValue,
        owner_table: str,
        name: str | None = None,
        expires_at: datetime | None = None,
        metadata: JsonObject | None = None,
        actor: AuditActor | None = None,
        secret_id: UUID | None = None,
    ) -> SecretRef:
        """Store a new secret and return its reference (version 1).

        Executed immediately, so the owner row inserted next can reference it.
        """
        secret_id = secret_id or uuid7()
        kind, payload = encode_value(value)
        kek_id, kek = await self._active_kek()
        wrapped, sealed = seal_value(
            kek_id=kek_id,
            kek=kek,
            secret_id=secret_id,
            version=1,
            kind=kind,
            purpose=purpose,
            scope=scope,
            payload=payload,
        )
        now = datetime.now(timezone.utc)
        await self._session.execute(
            insert(VaultSecret).values(
                id=secret_id,
                organization_id=scope.organization_id,
                pod_id=scope.pod_id,
                user_id=scope.user_id,
                purpose=purpose,
                kind=kind.value,
                owner_table=owner_table,
                name=name,
                alg=ALG,
                version=1,
                kek_id=kek_id,
                wrapped_dek=wrapped,
                ciphertext=sealed,
                expires_at=expires_at,
                metadata_=metadata or {},
                created_at=now,
                updated_at=now,
            )
        )
        await record_event(
            self._session,
            secret_id=secret_id,
            organization_id=scope.organization_id,
            purpose=purpose,
            action=SecretAction.CREATED,
            actor=actor or AuditActor.system(),
            version=1,
        )
        return SecretRef(secret_id, 1)

    async def replace(
        self,
        secret_id: UUID,
        *,
        expect: SecretScope,
        purpose: str,
        value: SecretValue,
        expected_version: int | None = None,
        lease: LeaseToken | None = None,
        expires_at: datetime | None | KeepExpiry = KEEP,
        skip_if_equal: bool = False,
        actor: AuditActor | None = None,
    ) -> SecretRef:
        """Write a new value under a new data key; the id stays the same.

        ``expected_version`` makes it a compare-and-set: a writer that read an
        older version gets :class:`SecretVersionConflict` instead of silently
        overwriting a newer value. Holding ``lease`` ends it.
        """
        # Keys before the row lock: loading them may hand the connection back
        # (see `_active_kek`), which must not happen while the row is locked.
        kek_id, kek = await self._active_kek()
        row = await self._row(secret_id, expect, purpose, for_update=True)
        if expected_version is not None and row.version != expected_version:
            raise SecretVersionConflict(secret_id, expected_version, row.version)
        if lease is not None and row.lease_holder != lease.holder:
            raise SecretVersionConflict(secret_id, lease.version, row.version)
        kind, payload = encode_value(value)
        old_kek = self.keyring.cached_encryption_key(row.kek_id)
        # An optimisation only: under a KEK this process has not loaded yet,
        # the value is simply written.
        if skip_if_equal and kind.value == row.kind and old_kek is not None:
            if self._decrypt_row_with(row, expect, old_kek) == payload:
                return SecretRef(secret_id, row.version)
        version = row.version + 1
        wrapped, sealed = seal_value(
            kek_id=kek_id,
            kek=kek,
            secret_id=secret_id,
            version=version,
            kind=kind,
            purpose=purpose,
            scope=expect,
            payload=payload,
        )
        values: dict[str, object] = {
            "kind": kind.value,
            "version": version,
            "kek_id": kek_id,
            "wrapped_dek": wrapped,
            "ciphertext": sealed,
            "lease_holder": None,
            "lease_until": None,
            "updated_at": datetime.now(timezone.utc),
        }
        if not isinstance(expires_at, KeepExpiry):
            values["expires_at"] = expires_at
        await self._session.execute(
            update(VaultSecret).where(VaultSecret.id == secret_id).values(**values)
        )
        await record_event(
            self._session,
            secret_id=secret_id,
            organization_id=expect.organization_id,
            purpose=purpose,
            action=SecretAction.REPLACED,
            actor=actor or AuditActor.system(),
            version=version,
        )
        return SecretRef(secret_id, version)

    async def delete(
        self,
        secret_id: UUID,
        *,
        expect: SecretScope,
        purpose: str,
        actor: AuditActor | None = None,
    ) -> bool:
        """Delete a secret no row owns (an owned one goes with its owner)."""
        try:
            await self._row(secret_id, expect, purpose)
        except SecretNotFound:
            return False
        await self._session.execute(
            delete(VaultSecret).where(VaultSecret.id == secret_id)
        )
        await record_event(
            self._session,
            secret_id=secret_id,
            organization_id=expect.organization_id,
            purpose=purpose,
            action=SecretAction.DELETED,
            actor=actor or AuditActor.system(),
            version=None,
        )
        return True

    # ------------------------------------------------------------------- read
    async def reveal(
        self,
        secret_id: UUID,
        *,
        expect: SecretScope,
        purpose: str,
        actor: AuditActor | None = None,
    ) -> Revealed:
        """Decrypt a secret. ``actor`` records the read (see ``audit``)."""
        row = await self._row(secret_id, expect, purpose)
        revealed = await self._reveal_row(row, expect)
        if actor is not None:
            await record_event(
                self._session,
                secret_id=secret_id,
                organization_id=expect.organization_id,
                purpose=purpose,
                action=SecretAction.REVEALED,
                actor=actor,
                version=row.version,
            )
        return revealed

    async def reveal_many(
        self, expected: Mapping[UUID, SecretScope], *, purpose: str
    ) -> dict[UUID, Revealed]:
        """Decrypt several secrets in one query. A missing id is left out."""
        if not expected:
            return {}
        result = await self._session.execute(
            select(*_COLUMNS).where(VaultSecret.id.in_(list(expected)))
        )
        revealed: dict[UUID, Revealed] = {}
        for row in [_Stored(*values) for values in result.all()]:
            scope = expected[row.id]
            _check_scope(row, scope, purpose)
            revealed[row.id] = await self._reveal_row(row, scope)
        return revealed

    async def meta(
        self, secret_id: UUID, *, expect: SecretScope, purpose: str
    ) -> SecretMeta | None:
        try:
            row = await self._row(secret_id, expect, purpose)
        except SecretNotFound:
            return None
        return SecretMeta(
            row.id, row.version, row.expires_at, row.lease_until, row.updated_at
        )

    # ------------------------------------------------------------------ lease
    async def try_lease(
        self,
        secret_id: UUID,
        *,
        expect: SecretScope,
        purpose: str,
        holder: str,
        ttl: timedelta,
        if_version: int,
    ) -> LeaseToken | None:
        """Take the secret's lease if nobody holds one and it is still ``if_version``.

        For a refresh that calls out to a provider: exactly one caller gets the
        lease and refreshes; the rest wait for the version to move. The caller
        commits after taking it, so others can see it, and holds no connection
        while it makes the outbound call.
        """
        now = func.now()
        result = await self._session.execute(
            update(VaultSecret)
            .where(
                VaultSecret.id == secret_id,
                VaultSecret.purpose == purpose,
                VaultSecret.organization_id.is_not_distinct_from(
                    expect.organization_id
                ),
                VaultSecret.pod_id.is_not_distinct_from(expect.pod_id),
                VaultSecret.user_id.is_not_distinct_from(expect.user_id),
                VaultSecret.version == if_version,
                or_(VaultSecret.lease_until.is_(None), VaultSecret.lease_until < now),
            )
            .values(lease_holder=holder, lease_until=now + ttl)
            .returning(VaultSecret.version)
        )
        version = result.scalar_one_or_none()
        return None if version is None else LeaseToken(secret_id, holder, version)

    async def release_lease(self, token: LeaseToken) -> None:
        await self._session.execute(
            update(VaultSecret)
            .where(
                VaultSecret.id == token.secret_id,
                VaultSecret.lease_holder == token.holder,
            )
            .values(lease_holder=None, lease_until=None)
        )

    # -------------------------------------------------------------- internals
    async def _row(
        self,
        secret_id: UUID,
        expect: SecretScope,
        purpose: str,
        *,
        for_update: bool = False,
    ) -> _Stored:
        stmt = select(*_COLUMNS).where(VaultSecret.id == secret_id)
        if for_update:
            stmt = stmt.with_for_update()
        values = (await self._session.execute(stmt)).first()
        if values is None:
            raise SecretNotFound(secret_id)
        row = _Stored(*values)
        _check_scope(row, expect, purpose)
        return row

    async def _active_kek(self) -> tuple[UUID, bytes]:
        cached = self.keyring.cached_active_encryption_key()
        if cached is None:
            await self._load_keys()
            cached = self.keyring.cached_active_encryption_key()
        if cached is None:
            raise VaultUnavailable("no active vault encryption key")
        return cached

    async def _kek(self, kek_id: UUID) -> bytes:
        key = self.keyring.cached_encryption_key(kek_id)
        if key is None:
            await self._load_keys()
            key = self.keyring.cached_encryption_key(kek_id)
        if key is None:
            raise VaultUnavailable(f"vault key {kek_id} is retired or missing")
        return key

    async def _load_keys(self) -> None:
        """Load keys this process does not have yet -- rare, and slow.

        Keys are loaded when the process starts, so this runs only for the
        first use in a process that skipped that (a script, a test) or for a
        KEK another process created since the last refresh. Loading may call the
        root key over the network, so the connection is handed back first.
        """
        async with connection_released(self._session):
            await self.keyring.load()

    async def _decrypt_row(self, row: _Stored, scope: SecretScope) -> bytes:
        return self._decrypt_row_with(row, scope, await self._kek(row.kek_id))

    def _decrypt_row_with(self, row: _Stored, scope: SecretScope, kek: bytes) -> bytes:
        try:
            return open_value(
                kek_id=row.kek_id,
                kek=kek,
                secret_id=row.id,
                version=row.version,
                kind=SecretKind(row.kind),
                purpose=row.purpose,
                scope=scope,
                wrapped_dek=row.wrapped_dek,
                ciphertext=row.ciphertext,
            )
        except InvalidTag as exc:
            logger.error(
                "vault.secret.integrity_check.failed",
                vault_row_id=str(row.id),
                purpose=row.purpose,
            )
            raise SecretIntegrityError(row.id) from exc

    async def _reveal_row(self, row: _Stored, scope: SecretScope) -> Revealed:
        payload = await self._decrypt_row(row, scope)
        return Revealed(
            row.id, row.version, SecretKind(row.kind), row.expires_at, payload
        )


def _check_scope(row: _Stored, expect: SecretScope, purpose: str) -> None:
    """A cheap, explicit check ahead of the cryptographic one.

    The associated data would refuse the decryption anyway; this makes the
    refusal a clear not-found, and logs it, since a caller holding the id of a
    secret outside its scope is either a bug or someone trying.
    """
    if (
        row.purpose != purpose
        or row.organization_id != expect.organization_id
        or row.pod_id != expect.pod_id
        or row.user_id != expect.user_id
    ):
        logger.error(
            "vault.secret.scope_mismatch.failed",
            vault_row_id=str(row.id),
            purpose=purpose,
        )
        raise SecretScopeMismatch(row.id)


def vault_for(source: SqlAlchemyUnitOfWork | AsyncSession) -> SqlVault:
    """The vault on ``source``'s transaction."""
    session = source.session if isinstance(source, SqlAlchemyUnitOfWork) else source
    return SqlVault(session)


async def reveal_values(
    vault: SqlVault, ids: Sequence[tuple[UUID, SecretScope]], *, purpose: str
) -> dict[UUID, SecretValue]:
    """``reveal_many``, unwrapped to plain values. For list reads."""
    revealed = await vault.reveal_many(dict(ids), purpose=purpose)
    return {secret_id: item.value() for secret_id, item in revealed.items()}
