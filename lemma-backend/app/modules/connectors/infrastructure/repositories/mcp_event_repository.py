"""Reads and writes for an install's events and the subscriptions made to them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.domain.account import AccountStatus
from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.mcp_events import DiscoveredEvent
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.mcp_event import (
    AuthConfigEvent,
    ConnectorEventSubscription,
)

#: One refresh pass renews at most this many; the next pass takes the rest.
REFRESH_BATCH = 200

#: Offers one person sees in the catalog, across every server they connected.
MAX_OFFERS = 200


@dataclass(frozen=True, slots=True)
class StoredEventSubscription:
    id: UUID
    organization_id: UUID
    auth_config_id: UUID
    account_id: UUID
    user_id: UUID
    name: str
    arguments: dict[str, object]
    secret_ciphertext: str
    remote_id: str | None
    granted_at: datetime | None
    refresh_before: datetime | None
    renew_failures: int = 0
    created_at: datetime | None = None
    last_error: str | None = None
    last_event_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EventOffer:
    """One event a person's connected server would tell them about."""

    account_id: UUID
    install_name: str
    event: DiscoveredEvent


def _stored(row: ConnectorEventSubscription) -> StoredEventSubscription:
    return StoredEventSubscription(
        id=row.id,
        organization_id=row.organization_id,
        auth_config_id=row.auth_config_id,
        account_id=row.account_id,
        user_id=row.user_id,
        name=row.name,
        arguments=dict(row.arguments or {}),
        secret_ciphertext=row.secret_ciphertext,
        remote_id=row.remote_id,
        granted_at=row.granted_at,
        refresh_before=row.refresh_before,
        renew_failures=row.renew_failures,
        created_at=row.created_at,
        last_error=row.last_error,
        last_event_at=row.last_event_at,
    )


def _event(row: AuthConfigEvent) -> DiscoveredEvent:
    return DiscoveredEvent(
        name=row.name,
        description=row.description,
        input_schema=dict(row.input_schema or {}),
        payload_schema=dict(row.payload_schema or {}),
    )


class McpEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_install_events(
        self,
        *,
        auth_config_id: UUID,
        organization_id: UUID,
        events: list[DiscoveredEvent],
    ) -> None:
        """Upsert what the server lists now, then drop what it no longer does.

        Upsert first, for the reason the operations table does: a delete that
        runs before a failing insert leaves the install with nothing.
        """
        for event in events:
            await self._session.execute(
                insert(AuthConfigEvent)
                .values(
                    auth_config_id=auth_config_id,
                    organization_id=organization_id,
                    name=event.name,
                    description=event.description,
                    input_schema=event.input_schema,
                    payload_schema=event.payload_schema,
                )
                .on_conflict_do_update(
                    index_elements=["auth_config_id", "name"],
                    set_={
                        "description": event.description,
                        "input_schema": event.input_schema,
                        "payload_schema": event.payload_schema,
                    },
                )
            )
        stale = delete(AuthConfigEvent).where(
            AuthConfigEvent.auth_config_id == auth_config_id
        )
        if events:
            stale = stale.where(
                AuthConfigEvent.name.not_in([event.name for event in events])
            )
        await self._session.execute(stale)

    async def install_event(
        self, auth_config_id: UUID, name: str
    ) -> DiscoveredEvent | None:
        row = (
            await self._session.execute(
                select(AuthConfigEvent).where(
                    AuthConfigEvent.auth_config_id == auth_config_id,
                    AuthConfigEvent.name == name,
                )
            )
        ).scalar_one_or_none()
        return _event(row) if row is not None else None

    async def offers_for(
        self, *, user_id: UUID, organization_id: UUID
    ) -> list[EventOffer]:
        """Every event on every MCP server this person has connected here."""
        rows = (
            await self._session.execute(
                select(Account.id, AuthConfig.name, AuthConfigEvent)
                .join(AuthConfig, AuthConfig.id == Account.auth_config_id)
                .join(
                    AuthConfigEvent,
                    AuthConfigEvent.auth_config_id == Account.auth_config_id,
                )
                .where(
                    Account.user_id == user_id,
                    Account.organization_id == organization_id,
                    Account.status == AccountStatus.CONNECTED.value,
                    AuthConfig.kind == ConnectorKind.MCP.value,
                )
                .order_by(AuthConfig.name, AuthConfigEvent.name)
                .limit(MAX_OFFERS)
            )
        ).all()
        return [
            EventOffer(account_id=account_id, install_name=name, event=_event(row))
            for account_id, name, row in rows
        ]

    async def add_subscription(
        self,
        *,
        organization_id: UUID,
        auth_config_id: UUID,
        account_id: UUID,
        user_id: UUID,
        name: str,
        arguments: dict[str, object],
        secret_ciphertext: str,
    ) -> UUID:
        row = ConnectorEventSubscription(
            organization_id=organization_id,
            auth_config_id=auth_config_id,
            account_id=account_id,
            user_id=user_id,
            name=name,
            arguments=arguments,
            secret_ciphertext=secret_ciphertext,
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def subscription(
        self, subscription_id: UUID
    ) -> StoredEventSubscription | None:
        row = await self._session.get(ConnectorEventSubscription, subscription_id)
        return _stored(row) if row is not None else None

    async def granted(
        self,
        subscription_id: UUID,
        *,
        remote_id: str | None,
        granted_at: datetime,
        refresh_before: datetime,
        renew_after: datetime,
    ) -> None:
        await self._session.execute(
            update(ConnectorEventSubscription)
            .where(ConnectorEventSubscription.id == subscription_id)
            .values(
                remote_id=remote_id,
                granted_at=granted_at,
                refresh_before=refresh_before,
                renew_after=renew_after,
                renew_failures=0,
                last_error=None,
            )
        )

    async def renewal_failed(
        self,
        subscription_id: UUID,
        *,
        error: str,
        failures: int,
        retry_after: datetime,
    ) -> None:
        await self._session.execute(
            update(ConnectorEventSubscription)
            .where(ConnectorEventSubscription.id == subscription_id)
            .values(
                last_error=error[:500],
                renew_failures=failures,
                renew_after=retry_after,
            )
        )

    async def note(
        self,
        subscription_id: UUID,
        *,
        error: str | None = None,
        event_at: datetime | None = None,
    ) -> None:
        values: dict[str, object] = {"last_error": error[:500] if error else None}
        if event_at is not None:
            values["last_event_at"] = event_at
        await self._session.execute(
            update(ConnectorEventSubscription)
            .where(ConnectorEventSubscription.id == subscription_id)
            .values(**values)
        )

    async def drop_subscription(self, subscription_id: UUID) -> bool:
        removed = await self._session.execute(
            delete(ConnectorEventSubscription)
            .where(ConnectorEventSubscription.id == subscription_id)
            .returning(ConnectorEventSubscription.id)
        )
        return removed.scalar_one_or_none() is not None

    async def page_after(
        self, after: UUID | None, *, limit: int
    ) -> list[StoredEventSubscription]:
        """Every subscription, a page at a time in id order, for reconciling
        them against the schedules they were made for."""
        statement = select(ConnectorEventSubscription)
        if after is not None:
            statement = statement.where(ConnectorEventSubscription.id > after)
        rows = (
            await self._session.execute(
                statement.order_by(ConnectorEventSubscription.id).limit(limit)
            )
        ).scalars()
        return [_stored(row) for row in rows]

    async def subscriptions(self, ids: list[UUID]) -> list[StoredEventSubscription]:
        if not ids:
            return []
        rows = (
            await self._session.execute(
                select(ConnectorEventSubscription).where(
                    ConnectorEventSubscription.id.in_(ids)
                )
            )
        ).scalars()
        return [_stored(row) for row in rows]

    async def drop_stale_pending(self, before: datetime) -> int:
        """Pending rows nobody finished: subscribe writes one before asking the
        server and records the grant after, so a crash between leaves a row
        that is never renewed and verifies nothing worth having."""
        removed = await self._session.execute(
            delete(ConnectorEventSubscription)
            .where(
                ConnectorEventSubscription.granted_at.is_(None),
                ConnectorEventSubscription.created_at < before,
            )
            .returning(ConnectorEventSubscription.id)
        )
        return len(removed.scalars().all())

    async def due_for_renewal(self, now: datetime) -> list[StoredEventSubscription]:
        """Those whose renewal or retry is due, longest overdue first.

        Only what is due is selected, so the batch limit applies to work that
        needs doing. A failed renewal moves its own `renew_after` on, so one the
        server keeps refusing goes to the back rather than holding the front.
        """
        rows = (
            await self._session.execute(
                select(ConnectorEventSubscription)
                .where(ConnectorEventSubscription.renew_after <= now)
                .order_by(ConnectorEventSubscription.renew_after)
                .limit(REFRESH_BATCH)
            )
        ).scalars()
        return [_stored(row) for row in rows]


__all__ = [
    "EventOffer",
    "McpEventRepository",
    "StoredEventSubscription",
]
