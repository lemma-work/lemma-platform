"""An in-memory vault for unit tests.

Implements :class:`app.modules.vault.contracts.Vault` with the same refusals as
the real one -- a wrong scope or purpose is a not-found, a stale version is a
conflict, a held lease is not granted twice -- so a repository tested against
it is tested against the rules, not against a dictionary. What it does not do
is encrypt; the real store's crypto is covered by the vault module's own tests.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from app.core.crypto.aead import new_key

from app.modules.vault.contracts import (
    KEEP,
    AuditActor,
    JsonObject,
    KeepExpiry,
    LeaseToken,
    Revealed,
    SecretKind,
    SecretMeta,
    SecretNotFound,
    SecretRef,
    SecretScope,
    SecretScopeMismatch,
    SecretValue,
    SecretVersionConflict,
)


@dataclass
class FakeSecret:
    scope: SecretScope
    purpose: str
    owner_table: str
    value: SecretValue
    version: int
    expires_at: datetime | None
    updated_at: datetime
    name: str | None = None
    lease_holder: str | None = None
    lease_until: datetime | None = None


class FakeVault:
    def __init__(self) -> None:
        self.secrets: dict[UUID, FakeSecret] = {}
        self.reveals: list[tuple[UUID, AuditActor | None]] = []

    def value_of(self, secret_id: UUID) -> SecretValue:
        return copy.deepcopy(self.secrets[secret_id].value)

    def _get(self, secret_id: UUID, expect: SecretScope, purpose: str) -> FakeSecret:
        secret = self.secrets.get(secret_id)
        if secret is None:
            raise SecretNotFound(secret_id)
        if secret.scope != expect or secret.purpose != purpose:
            raise SecretScopeMismatch(secret_id)
        return secret

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
        secret_id = secret_id or uuid7()
        self.secrets[secret_id] = FakeSecret(
            scope,
            purpose,
            owner_table,
            copy.deepcopy(value),
            1,
            expires_at,
            datetime.now(timezone.utc),
            name,
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
        secret = self._get(secret_id, expect, purpose)
        if expected_version is not None and secret.version != expected_version:
            raise SecretVersionConflict(secret_id, expected_version, secret.version)
        if lease is not None and secret.lease_holder != lease.holder:
            raise SecretVersionConflict(secret_id, lease.version, secret.version)
        if skip_if_equal and secret.value == value:
            return SecretRef(secret_id, secret.version)
        secret.value = copy.deepcopy(value)
        secret.version += 1
        secret.lease_holder = secret.lease_until = None
        secret.updated_at = datetime.now(timezone.utc)
        if not isinstance(expires_at, KeepExpiry):
            secret.expires_at = expires_at
        return SecretRef(secret_id, secret.version)

    async def delete(
        self,
        secret_id: UUID,
        *,
        expect: SecretScope,
        purpose: str,
        actor: AuditActor | None = None,
    ) -> bool:
        try:
            self._get(secret_id, expect, purpose)
        except SecretNotFound:
            return False
        del self.secrets[secret_id]
        return True

    async def reveal(
        self,
        secret_id: UUID,
        *,
        expect: SecretScope,
        purpose: str,
        actor: AuditActor | None = None,
    ) -> Revealed:
        secret = self._get(secret_id, expect, purpose)
        self.reveals.append((secret_id, actor))
        return _revealed(secret_id, secret)

    async def reveal_many(
        self, expected: Mapping[UUID, SecretScope], *, purpose: str
    ) -> dict[UUID, Revealed]:
        return {
            secret_id: _revealed(secret_id, self._get(secret_id, scope, purpose))
            for secret_id, scope in expected.items()
            if secret_id in self.secrets
        }

    async def meta(
        self, secret_id: UUID, *, expect: SecretScope, purpose: str
    ) -> SecretMeta | None:
        try:
            secret = self._get(secret_id, expect, purpose)
        except SecretNotFound:
            return None
        return SecretMeta(
            secret_id,
            secret.version,
            secret.expires_at,
            secret.lease_until,
            secret.updated_at,
        )

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
        secret = self._get(secret_id, expect, purpose)
        now = datetime.now(timezone.utc)
        if secret.version != if_version:
            return None
        if secret.lease_until is not None and secret.lease_until > now:
            return None
        secret.lease_holder, secret.lease_until = holder, now + ttl
        return LeaseToken(secret_id, holder, secret.version)

    async def release_lease(self, token: LeaseToken) -> None:
        secret = self.secrets.get(token.secret_id)
        if secret is not None and secret.lease_holder == token.holder:
            secret.lease_holder = secret.lease_until = None


def _revealed(secret_id: UUID, secret: FakeSecret) -> Revealed:
    if isinstance(secret.value, str):
        return Revealed(
            secret_id,
            secret.version,
            SecretKind.TEXT,
            secret.expires_at,
            secret.value.encode(),
        )
    return Revealed(
        secret_id,
        secret.version,
        SecretKind.JSON,
        secret.expires_at,
        json.dumps(secret.value).encode(),
    )


class StaticSealingKeys:
    """Real keys, in memory: for testing code that seals values with the sealer.

    Implements ``SealingKeys``. ``rotate()`` makes a new active key and keeps
    the old one for opening, like a KEK rotation.
    """

    def __init__(self) -> None:
        self.keys: dict[UUID, bytes] = {}
        self.active = self.rotate()

    def rotate(self) -> UUID:
        key_id = uuid7()
        self.keys[key_id] = new_key()
        self.active = key_id
        return key_id

    async def active_encryption_key(self) -> tuple[UUID, bytes]:
        return self.active, self.keys[self.active]

    async def encryption_key(self, key_id: UUID) -> bytes:
        return self.keys[key_id]
