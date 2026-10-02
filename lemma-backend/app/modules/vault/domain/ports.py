"""The vault as a port, for callers that take it as a collaborator."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.modules.vault.domain.types import (
    AuditActor,
    JsonObject,
    LeaseToken,
    Revealed,
    SecretMeta,
    SecretRef,
    SecretScope,
    SecretValue,
)


class KeepExpiry:
    """Sentinel type: leave ``expires_at`` as it is on replace."""


KEEP = KeepExpiry()


class Vault(Protocol):
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
    ) -> SecretRef: ...

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
    ) -> SecretRef: ...

    async def delete(
        self,
        secret_id: UUID,
        *,
        expect: SecretScope,
        purpose: str,
        actor: AuditActor | None = None,
    ) -> bool: ...

    async def reveal(
        self,
        secret_id: UUID,
        *,
        expect: SecretScope,
        purpose: str,
        actor: AuditActor | None = None,
    ) -> Revealed: ...

    async def reveal_many(
        self, expected: Mapping[UUID, SecretScope], *, purpose: str
    ) -> dict[UUID, Revealed]: ...

    async def meta(
        self, secret_id: UUID, *, expect: SecretScope, purpose: str
    ) -> SecretMeta | None: ...

    async def try_lease(
        self,
        secret_id: UUID,
        *,
        expect: SecretScope,
        purpose: str,
        holder: str,
        ttl: timedelta,
        if_version: int,
    ) -> LeaseToken | None: ...

    async def release_lease(self, token: LeaseToken) -> None: ...
