"""Asking a person's connected MCP server to tell a schedule when something happens.

Subscribing has an order the draft forces. The server proves our callback
before it answers `events/subscribe`, by sending it a challenge signed with
the secret we gave it -- so the subscription, and that secret, must already be
committed where the webhook endpoint can find them when the challenge arrives.
A pending row first, then the call, then what the server granted.

A subscription lasts until the server's `refreshBefore`, and the refresher
renews each one halfway through what was granted. One that fails to renew is
retried later, waiting longer each time, so it never holds the place of one
that would renew. Nothing here holds a database connection across a call to
the server.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from pydantic import JsonValue

from app.core.config import settings
from app.core.crypto.factory import get_secret_cipher
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.connectors.domain.errors import (
    ConnectorDomainError,
    ConnectorInfrastructureError,
    ConnectorValidationError,
)
from app.modules.connectors.domain.mcp_events import (
    MCP_WEBHOOK_SOURCE,
    RENEW_PASS_BUDGET,
    REQUESTED_TTL,
    SUBSCRIPTION_PARAM,
    arguments_problem,
    new_secret,
    refresh_before_from,
    renew_at,
    retry_at,
)
from app.modules.connectors.infrastructure.adapters.mcp_events_client import (
    TRANSPORT_FAILURE,
    McpEventsClient,
    McpEventsError,
)
from app.modules.connectors.infrastructure.repositories.mcp_event_repository import (
    McpEventRepository,
    StoredEventSubscription,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class McpTarget:
    """Where one account's server is, and how to ask it."""

    server_url: str
    headers: dict[str, str]
    auth_config_id: UUID
    organization_id: UUID


#: The server behind this account, asked as this person.
TargetResolver = Callable[[SqlAlchemyUnitOfWork, UUID, UUID], Awaitable[McpTarget]]
ClientFactory = Callable[[str, dict[str, str]], McpEventsClient]
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def callback_url(subscription_id: UUID) -> str:
    return (
        f"{settings.api_url.rstrip('/')}/webhooks/{MCP_WEBHOOK_SOURCE}"
        f"?{SUBSCRIPTION_PARAM}={subscription_id}"
    )


def _refusal(event: str, exc: McpEventsError) -> Exception:
    """A server that could not be reached is ours to retry; one that said no
    is the author's to change."""
    if exc.code == TRANSPORT_FAILURE:
        return ConnectorInfrastructureError(
            f"Could not reach the server to listen for '{event}'."
        )
    return ConnectorValidationError(
        f"The server would not tell us about '{event}': {exc.message}"
    )


class McpEventSubscriptions:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        target: TargetResolver,
        client: ClientFactory = McpEventsClient,
        clock: Clock = _utcnow,
    ) -> None:
        self._uow_factory = uow_factory
        self._target = target
        self._client = client
        self._clock = clock

    async def subscribe(
        self,
        *,
        account_id: UUID,
        user_id: UUID,
        event: str,
        arguments: dict[str, JsonValue],
    ) -> UUID:
        secret = new_secret()
        async with self._uow_factory() as uow:
            target = await self._target(uow, account_id, user_id)
            events = McpEventRepository(uow.session)
            offered = await events.install_event(target.auth_config_id, event)
            if offered is None:
                raise ConnectorValidationError(
                    f"This server does not offer '{event}'. If it was added "
                    "since the server was connected, refresh its tools."
                )
            problem = arguments_problem(offered.input_schema, arguments)
            if problem:
                raise ConnectorValidationError(
                    f"'{event}' cannot be narrowed that way. {problem}"
                )
            subscription_id = await events.add_subscription(
                organization_id=target.organization_id,
                auth_config_id=target.auth_config_id,
                account_id=account_id,
                user_id=user_id,
                name=event,
                arguments=dict(arguments),
                secret_ciphertext=str(get_secret_cipher().encrypt_str(secret)),
            )
            await uow.commit()
        try:
            granted = await self._client(target.server_url, target.headers).subscribe(
                name=event,
                arguments=arguments,
                url=callback_url(subscription_id),
                secret=secret,
                ttl_ms=int(REQUESTED_TTL.total_seconds() * 1000),
            )
        except McpEventsError as exc:
            await self._forget(subscription_id)
            raise _refusal(event, exc) from exc
        await self._record_grant(subscription_id, granted)
        return subscription_id

    async def unsubscribe(self, subscription_id: UUID) -> None:
        """Best effort at the server, certain here: the row goes either way,
        so a delivery that still arrives finds nothing to verify against."""
        async with self._uow_factory() as uow:
            stored = await McpEventRepository(uow.session).subscription(subscription_id)
            target = None
            if stored is not None:
                target = await self._target_or_none(uow, stored)
        if stored is None:
            return
        if target is not None:
            try:
                await self._client(target.server_url, target.headers).unsubscribe(
                    name=stored.name,
                    arguments=dict(stored.arguments),
                    url=callback_url(stored.id),
                )
            except McpEventsError as exc:
                logger.warning(
                    "connectors.mcp_events.unsubscribe.degraded",
                    subscription_id=str(stored.id),
                    code=exc.code,
                )
        await self._forget(subscription_id)

    async def renew_due(self) -> int:
        """Renew what is due this pass; the count renewed.

        Stops starting renewals once the pass has spent its budget, so a slow
        server cannot run one pass into the next. What is left is still due,
        and the next pass takes it first.
        """
        started = self._clock()
        async with self._uow_factory() as uow:
            due = await McpEventRepository(uow.session).due_for_renewal(started)
        renewed = 0
        for attempted, stored in enumerate(due):
            if self._clock() - started >= RENEW_PASS_BUDGET:
                logger.warning(
                    "connectors.mcp_events.renew_budget_spent.degraded",
                    renewed_count=renewed,
                    left_count=len(due) - attempted,
                )
                break
            renewed += int(await self._renew(stored))
        return renewed

    async def listening(
        self, subscription_id: UUID
    ) -> tuple[StoredEventSubscription, str] | None:
        """The subscription a delivery names, and the secret to check it with."""
        async with self._uow_factory() as uow:
            stored = await McpEventRepository(uow.session).subscription(subscription_id)
        if stored is None:
            return None
        secret = get_secret_cipher().decrypt_str(stored.secret_ciphertext)
        return (stored, secret) if secret else None

    async def heard(self, subscription_id: UUID) -> None:
        async with self._uow_factory() as uow:
            await McpEventRepository(uow.session).note(
                subscription_id, event_at=self._clock()
            )
            await uow.commit()

    async def _renew(self, stored: StoredEventSubscription) -> bool:
        async with self._uow_factory() as uow:
            target = await self._target_or_none(uow, stored)
        secret = get_secret_cipher().decrypt_str(stored.secret_ciphertext)
        if target is None or not secret:
            await self._renewal_failed(stored, "account_unavailable")
            return False
        try:
            granted = await self._client(target.server_url, target.headers).subscribe(
                name=stored.name,
                arguments=dict(stored.arguments),
                url=callback_url(stored.id),
                secret=secret,
                ttl_ms=int(REQUESTED_TTL.total_seconds() * 1000),
            )
        except McpEventsError as exc:
            await self._renewal_failed(stored, f"{exc.code}: {exc.message}")
            return False
        await self._record_grant(stored.id, granted)
        return True

    async def _target_or_none(
        self, uow: SqlAlchemyUnitOfWork, stored: StoredEventSubscription
    ) -> McpTarget | None:
        """The account's server, or None when the account can no longer reach
        it -- disconnected, its credential gone."""
        try:
            return await self._target(uow, stored.account_id, stored.user_id)
        except ConnectorDomainError as exc:
            logger.info(
                "connectors.mcp_events.target_unavailable",
                subscription_id=str(stored.id),
                error_type=type(exc).__name__,
            )
            return None

    async def _record_grant(
        self, subscription_id: UUID, granted: dict[str, JsonValue]
    ) -> None:
        now = self._clock()
        remote = granted.get("id")
        refresh_before = refresh_before_from(granted.get("refreshBefore"), now=now)
        async with self._uow_factory() as uow:
            await McpEventRepository(uow.session).granted(
                subscription_id,
                remote_id=remote if isinstance(remote, str) else None,
                granted_at=now,
                refresh_before=refresh_before,
                renew_after=renew_at(granted_at=now, refresh_before=refresh_before),
            )
            await uow.commit()

    async def _renewal_failed(
        self, stored: StoredEventSubscription, error: str
    ) -> None:
        now = self._clock()
        failures = stored.renew_failures + 1
        async with self._uow_factory() as uow:
            await McpEventRepository(uow.session).renewal_failed(
                stored.id,
                error=error,
                failures=failures,
                retry_after=retry_at(
                    failures=failures,
                    refresh_before=stored.refresh_before or now,
                    now=now,
                ),
            )
            await uow.commit()

    async def _forget(self, subscription_id: UUID) -> None:
        async with self._uow_factory() as uow:
            await McpEventRepository(uow.session).drop_subscription(subscription_id)
            await uow.commit()
