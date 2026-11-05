"""Opt-outs, kept by the hash of the handle that asked.

Nothing here names a contact: a suppression is a fact about a handle, so it
holds before the handle is anybody's, after its contact is forgotten, and
across a merge.
"""

from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum
from uuid import UUID

from sqlalchemy import column, delete, exists, select, table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.contacts.domain.handles import handle_hash
from app.modules.contacts.domain.kinds import IdentityKind
from app.modules.contacts.infrastructure.models import ContactSuppressionModel


class SuppressionReason(StrEnum):
    #: They wrote STOP (or UNSUBSCRIBE, ...) from the handle.
    STOP = "stop"
    #: They followed an email's unsubscribe link, or its one-click header.
    UNSUBSCRIBE = "unsubscribe"
    #: The address does not take mail.
    BOUNCE = "bounce"
    #: They marked the pod's mail as spam.
    COMPLAINT = "complaint"


class SuppressionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def suppress(
        self,
        *,
        pod_id: UUID,
        kind: IdentityKind,
        value_hash: bytes,
        reason: SuppressionReason,
    ) -> bool:
        """Opt the handle out; whether it was not already."""
        result = await self.session.execute(
            insert(ContactSuppressionModel)
            .values(
                pod_id=pod_id,
                kind=kind.value,
                value_hash=value_hash,
                reason=reason.value,
            )
            .on_conflict_do_nothing()
        )
        return bool(result.rowcount)

    async def pod_exists(self, pod_id: UUID) -> bool:
        """Whether the pod is still there to keep an opt-out against."""
        pods = table("pods", column("id"))
        return bool(
            await self.session.scalar(select(exists().where(pods.c.id == pod_id)))
        )

    async def lift(self, *, pod_id: UUID, kind: IdentityKind, value: str) -> bool:
        """Opt the handle back in; whether it was opted out."""
        result = await self.session.execute(
            delete(ContactSuppressionModel).where(
                ContactSuppressionModel.pod_id == pod_id,
                ContactSuppressionModel.kind == kind.value,
                ContactSuppressionModel.value_hash == handle_hash(kind, value),
            )
        )
        return bool(result.rowcount)

    async def suppressed(
        self, *, pod_id: UUID, kind: IdentityKind, values: Collection[str]
    ) -> set[str]:
        """Which of these normalised values of one kind are opted out."""
        by_hash = {handle_hash(kind, value): value for value in values if value}
        if not by_hash:
            return set()
        rows = await self.session.scalars(
            select(ContactSuppressionModel.value_hash).where(
                ContactSuppressionModel.pod_id == pod_id,
                ContactSuppressionModel.kind == kind.value,
                ContactSuppressionModel.value_hash.in_(list(by_hash)),
            )
        )
        return {by_hash[bytes(found)] for found in rows}
