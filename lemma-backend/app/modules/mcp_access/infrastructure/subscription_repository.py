"""Reads and writes for outside clients' event subscriptions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.mcp_access.infrastructure.models import (
    McpEventSubscription,
    McpOAuthGrant,
)

#: One row event never fans out to more deliveries than this.
MAX_DELIVERIES_PER_EVENT = 200


@dataclass(frozen=True, slots=True)
class StoredSubscription:
    public_id: str
    grant_id: UUID
    user_id: UUID
    pod_id: UUID
    name: str
    arguments: dict[str, object]
    url: str
    secret_ciphertext: str
    refresh_before: datetime
    verified_at: datetime | None
    last_delivery_at: datetime | None
    last_error: str | None


def _stored(row: McpEventSubscription) -> StoredSubscription:
    return StoredSubscription(
        public_id=row.public_id,
        grant_id=row.grant_id,
        user_id=row.user_id,
        pod_id=row.pod_id,
        name=row.name,
        arguments=dict(row.arguments or {}),
        url=row.url,
        secret_ciphertext=row.secret_ciphertext,
        refresh_before=row.refresh_before,
        verified_at=row.verified_at,
        last_delivery_at=row.last_delivery_at,
        last_error=row.last_error,
    )


class EventSubscriptionRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._session = uow.session

    async def upsert(
        self,
        *,
        public_id: str,
        grant_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        name: str,
        arguments: dict[str, object],
        arguments_key: str,
        url: str,
        secret_ciphertext: str,
        refresh_before: datetime,
        verified_at: datetime | None,
        now: datetime,
    ) -> StoredSubscription:
        """Create the subscription, or refresh the one with this identity.

        A refresh may bring a new secret (the draft's rotation): it replaces
        the stored one, and the old one signs nothing further.
        """
        values = {
            "public_id": public_id,
            "grant_id": grant_id,
            "user_id": user_id,
            "pod_id": pod_id,
            "name": name,
            "arguments": arguments,
            "arguments_key": arguments_key,
            "url": url,
            "secret_ciphertext": secret_ciphertext,
            "refresh_before": refresh_before,
            "verified_at": verified_at,
            "created_at": now,
            "updated_at": now,
        }
        statement = (
            insert(McpEventSubscription)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["grant_id", "url", "name", "arguments_key"],
                set_={
                    "secret_ciphertext": secret_ciphertext,
                    "refresh_before": refresh_before,
                    "verified_at": func.coalesce(
                        McpEventSubscription.verified_at, verified_at
                    ),
                    "updated_at": now,
                },
            )
            .returning(McpEventSubscription)
        )
        row = (await self._session.execute(statement)).scalar_one()
        return _stored(row)

    async def get(self, public_id: str) -> StoredSubscription | None:
        row = (
            await self._session.execute(
                select(McpEventSubscription).where(
                    McpEventSubscription.public_id == public_id
                )
            )
        ).scalar_one_or_none()
        return _stored(row) if row is not None else None

    async def remove(self, public_id: str) -> bool:
        removed = await self._session.execute(
            delete(McpEventSubscription)
            .where(McpEventSubscription.public_id == public_id)
            .returning(McpEventSubscription.id)
        )
        return removed.scalar_one_or_none() is not None

    async def count_for_grant(self, grant_id: UUID) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(McpEventSubscription)
                .where(McpEventSubscription.grant_id == grant_id)
            )
            or 0
        )

    async def url_verified(self, grant_id: UUID, url: str) -> bool:
        """Whether this connection already proved it controls this URL."""
        return bool(
            await self._session.scalar(
                select(
                    select(1)
                    .where(
                        McpEventSubscription.grant_id == grant_id,
                        McpEventSubscription.url == url,
                        McpEventSubscription.verified_at.is_not(None),
                    )
                    .exists()
                )
            )
        )

    async def pod_is_subscribed(self, pod_id: UUID, *, now: datetime) -> bool:
        """The cheap question asked of every row written anywhere."""
        return bool(
            await self._session.scalar(
                select(
                    select(1)
                    .where(
                        McpEventSubscription.pod_id == pod_id,
                        McpEventSubscription.refresh_before > now,
                    )
                    .exists()
                )
            )
        )

    async def live_for(
        self, *, pod_id: UUID, name: str, table: str, now: datetime
    ) -> list[StoredSubscription]:
        rows = (
            await self._session.execute(
                select(McpEventSubscription)
                .where(
                    McpEventSubscription.pod_id == pod_id,
                    McpEventSubscription.name == name,
                    McpEventSubscription.refresh_before > now,
                    McpEventSubscription.verified_at.is_not(None),
                    func.jsonb_extract_path_text(
                        McpEventSubscription.arguments, "table"
                    )
                    == table,
                )
                .limit(MAX_DELIVERIES_PER_EVENT)
            )
        ).scalars()
        return [_stored(row) for row in rows]

    async def record_delivery(
        self, public_id: str, *, now: datetime, error: str | None
    ) -> None:
        values: dict[str, object] = {"last_error": error[:500] if error else None}
        if error is None:
            values["last_delivery_at"] = now
        await self._session.execute(
            update(McpEventSubscription)
            .where(McpEventSubscription.public_id == public_id)
            .values(**values)
        )

    async def for_grants(self, grant_ids: list[UUID]) -> list[StoredSubscription]:
        """What these connections listen to, for the list a person reads."""
        if not grant_ids:
            return []
        rows = (
            await self._session.execute(
                select(McpEventSubscription)
                .where(McpEventSubscription.grant_id.in_(grant_ids))
                .order_by(McpEventSubscription.created_at)
                .limit(MAX_DELIVERIES_PER_EVENT)
            )
        ).scalars()
        return [_stored(row) for row in rows]

    async def remove_for_grant(self, *, grant_id: UUID, public_id: str) -> bool:
        removed = await self._session.execute(
            delete(McpEventSubscription)
            .where(
                McpEventSubscription.grant_id == grant_id,
                McpEventSubscription.public_id == public_id,
            )
            .returning(McpEventSubscription.id)
        )
        return removed.scalar_one_or_none() is not None

    async def for_user(self, user_id: UUID) -> list[StoredSubscription]:
        """What the person's live connections listen to, for Settings."""
        rows = (
            await self._session.execute(
                select(McpEventSubscription)
                .join(McpOAuthGrant, McpOAuthGrant.id == McpEventSubscription.grant_id)
                .where(
                    McpEventSubscription.user_id == user_id,
                    McpOAuthGrant.revoked_at.is_(None),
                )
                .order_by(McpEventSubscription.created_at)
                .limit(MAX_DELIVERIES_PER_EVENT)
            )
        ).scalars()
        return [_stored(row) for row in rows]
