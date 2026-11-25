"""The durable record of what happened to a secret.

Written on the caller's session, so an event commits with the change it
describes or not at all. Every change is recorded. Reads are recorded when
someone asked for them on purpose -- a person, or a workload resolving a named
secret -- and not when the platform reads a credential to make an API call:
that is every tool call, and a row per call would double the write load for a
record nobody could act on. Those reads are in the structured logs instead.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid7

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_context import get_request_id

from app.modules.vault.domain.types import AuditActor, JsonObject
from app.modules.vault.infrastructure.models import VaultSecretEvent


class SecretAction(StrEnum):
    CREATED = "created"
    REPLACED = "replaced"
    REVEALED = "revealed"
    DELETED = "deleted"
    REWRAPPED = "rewrapped"
    IMPORTED = "imported"


async def record_event(
    session: AsyncSession,
    *,
    secret_id: UUID,
    organization_id: UUID | None,
    purpose: str | None,
    action: SecretAction,
    actor: AuditActor,
    version: int | None,
    detail: JsonObject | None = None,
) -> None:
    await session.execute(
        insert(VaultSecretEvent).values(
            id=uuid7(),
            secret_id=secret_id,
            organization_id=organization_id,
            purpose=purpose,
            action=action.value,
            actor_kind=actor.kind.value,
            actor_id=actor.id,
            version=version,
            request_id=get_request_id(),
            detail=detail or {},
        )
    )
