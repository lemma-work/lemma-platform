"""Reads and writes for outside clients' event subscriptions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.mcp_access.infrastructure.models import (
    McpEventSubscription,
    McpOAuthGrant,
)

#: Subscriptions read per page when one row event fans out.
FAN_OUT_PAGE = 200

#: At most this many subscriptions are listed for a page of connections.
MAX_LISTED = 500

#: Deliveries the receiver did not take in a row before delivery pauses. The
#: client's next refresh -- proof it is still there -- resumes it.
PAUSE_AFTER_FAILURES = 20


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
    stopped_at: datetime | None = None
    paused_at: datetime | None = None
    failures: int = 0


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
        stopped_at=row.stopped_at,
        paused_at=row.paused_at,
        failures=row.failures or 0,
    )


def _delivering(now: datetime):
    """Live, not stopped by the person, not paused by failures."""
    return (
        McpEventSubscription.refresh_before > now,
        McpEventSubscription.stopped_at.is_(None),
        McpEventSubscription.paused_at.is_(None),
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
        the stored one, and the old one signs nothing further. A refresh is
        also the client showing it is still there, so it lifts a pause that
        failed deliveries set. It never lifts the person's Stop: a stopped
        subscription is refused before it gets here.
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
                    "paused_at": None,
                    "failures": 0,
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

    async def remove_unless_stopped(self, public_id: str) -> None:
        """The client's `events/unsubscribe`. A stopped row is the person's
        tombstone, so the client cannot clear it by unsubscribing and
        subscribing again."""
        await self._session.execute(
            delete(McpEventSubscription).where(
                McpEventSubscription.public_id == public_id,
                McpEventSubscription.stopped_at.is_(None),
            )
        )

    async def lock_grant(self, grant_id: UUID) -> None:
        """Serialize subscribing on one connection, so the cap is counted and
        the row written as one step."""
        await self._session.execute(
            select(McpOAuthGrant.id)
            .where(McpOAuthGrant.id == grant_id)
            .with_for_update()
        )

    async def count_for_grant(self, grant_id: UUID, *, now: datetime) -> int:
        """Live subscriptions: a lapsed one costs nothing and the client may
        have moved on from it, and a stopped one is the person's, not the
        client's."""
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(McpEventSubscription)
                .where(
                    McpEventSubscription.grant_id == grant_id,
                    McpEventSubscription.refresh_before > now,
                    McpEventSubscription.stopped_at.is_(None),
                )
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
                    .where(McpEventSubscription.pod_id == pod_id, *_delivering(now))
                    .exists()
                )
            )
        )

    async def live_for(
        self,
        *,
        pod_id: UUID,
        name: str,
        table: str,
        now: datetime,
        after: str | None = None,
    ) -> list[StoredSubscription]:
        """One page of the subscriptions a new row in this table goes to, in
        id order after ``after``; the caller pages until one comes back short."""
        rows = list(
            (
                await self._session.execute(
                    select(McpEventSubscription)
                    .where(
                        McpEventSubscription.pod_id == pod_id,
                        McpEventSubscription.name == name,
                        McpEventSubscription.verified_at.is_not(None),
                        func.jsonb_extract_path_text(
                            McpEventSubscription.arguments, "table"
                        )
                        == table,
                        McpEventSubscription.public_id > (after or ""),
                        *_delivering(now),
                    )
                    .order_by(McpEventSubscription.public_id)
                    .limit(FAN_OUT_PAGE)
                )
            ).scalars()
        )
        return [_stored(row) for row in rows]

    async def record_delivery(
        self, public_id: str, *, now: datetime, error: str | None
    ) -> None:
        """A delivery's outcome. Failures in a row are counted, and past
        `PAUSE_AFTER_FAILURES` delivery pauses rather than retrying into a
        receiver that has gone."""
        if error is None:
            values: dict[str, object] = {
                "last_error": None,
                "last_delivery_at": now,
                "failures": 0,
            }
        else:
            failures = McpEventSubscription.failures + 1
            values = {
                "last_error": error[:500],
                "failures": failures,
                "paused_at": case(
                    (failures >= PAUSE_AFTER_FAILURES, now),
                    else_=McpEventSubscription.paused_at,
                ),
            }
        await self._session.execute(
            update(McpEventSubscription)
            .where(McpEventSubscription.public_id == public_id)
            .values(**values)
        )

    async def for_grants(
        self, grant_ids: list[UUID], *, now: datetime
    ) -> list[StoredSubscription]:
        """What these connections listen to, for the list a person reads: the
        live ones and the ones the person stopped -- not those the client let
        lapse, which are no longer anything."""
        if not grant_ids:
            return []
        rows = (
            await self._session.execute(
                select(McpEventSubscription)
                .where(
                    McpEventSubscription.grant_id.in_(grant_ids),
                    or_(
                        McpEventSubscription.refresh_before > now,
                        McpEventSubscription.stopped_at.is_not(None),
                    ),
                )
                .order_by(McpEventSubscription.created_at)
                .limit(MAX_LISTED)
            )
        ).scalars()
        return [_stored(row) for row in rows]

    async def set_stopped_for_grant(
        self, *, grant_id: UUID, public_id: str, stopped_at: datetime | None
    ) -> bool:
        """Stop (a time) or resume (None) one of a connection's subscriptions."""
        changed = await self._session.execute(
            update(McpEventSubscription)
            .where(
                McpEventSubscription.grant_id == grant_id,
                McpEventSubscription.public_id == public_id,
            )
            .values(stopped_at=stopped_at, paused_at=None, failures=0)
            .returning(McpEventSubscription.id)
        )
        return changed.scalar_one_or_none() is not None

    async def sweep(self, *, lapsed_before: datetime, batch: int) -> int:
        """Delete what can never deliver again: subscriptions the client let
        lapse long ago, and every subscription of a revoked connection (revoke
        keeps the grant row, so its `CASCADE` never fires)."""
        revoked = exists().where(
            McpOAuthGrant.id == McpEventSubscription.grant_id,
            McpOAuthGrant.revoked_at.is_not(None),
        )
        doomed = (
            select(McpEventSubscription.id)
            .where(
                or_(
                    revoked,
                    (McpEventSubscription.refresh_before < lapsed_before)
                    & McpEventSubscription.stopped_at.is_(None),
                )
            )
            .limit(batch)
        )
        removed = await self._session.execute(
            delete(McpEventSubscription)
            .where(McpEventSubscription.id.in_(doomed))
            .returning(McpEventSubscription.id)
        )
        return len(removed.scalars().all())
