"""Subscribing an outside client to a pod's events, and letting it go.

What `events/subscribe` and `events/unsubscribe` do, in the order the draft and
ChatGPT's integration both require: the connection may read, the event exists,
the arguments make sense, the person can read what they name, the callback is
one we may call and proves it is the client's, and only then is anything
stored.
"""

from __future__ import annotations

import hmac
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from pydantic import JsonValue

from app.core.crypto.factory import get_secret_cipher
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.net.url_guard import UnsafeUrlError, assert_safe_url
from app.modules.mcp_access.domain.entities import McpPrincipal, Scope
from app.modules.mcp_access.domain.events import (
    CALLBACK_ENDPOINT_ERROR,
    DESCRIPTORS,
    EVENT_NAMES,
    FORBIDDEN,
    INVALID_PARAMS,
    MAX_SUBSCRIPTIONS_PER_GRANT,
    NOT_FOUND,
    RESOURCE_EXHAUSTED,
    UNSUPPORTED,
    canonical_arguments,
    granted_ttl,
    subscription_id,
    valid_secret,
)
from app.modules.mcp_access.infrastructure.subscription_repository import (
    EventSubscriptionRepository,
)
from app.modules.mcp_access.infrastructure.webhook_sender import (
    SendResult,
    send_signed,
)

Clock = Callable[[], datetime]
#: Whether this person can read this table in this pod, now.
TableAccess = Callable[[UUID, UUID, str], Awaitable[bool]]
Sender = Callable[..., Awaitable[SendResult]]


class EventSubscriptionError(Exception):
    """An answer for the client, carried as an MCP error with the draft's code."""

    def __init__(
        self, code: int, message: str, data: dict[str, JsonValue] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass(frozen=True, slots=True)
class SubscribeRequest:
    name: str
    arguments: dict[str, JsonValue]
    mode: str
    url: str
    secret: str
    ttl_ms: int | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return (
        value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if value
        else None
    )


def list_events(principal: McpPrincipal | None) -> dict[str, JsonValue]:
    _require_reader(principal)
    return {"events": list(DESCRIPTORS)}


def _require_reader(principal: McpPrincipal | None) -> McpPrincipal:
    if principal is None or not principal.allows(Scope.READ):
        raise EventSubscriptionError(
            FORBIDDEN, "Events are for a connected app with read access."
        )
    return principal


def _table_argument(arguments: dict[str, JsonValue]) -> str:
    table = arguments.get("table")
    if not isinstance(table, str) or not table.strip() or set(arguments) - {"table"}:
        raise EventSubscriptionError(
            INVALID_PARAMS, "record.created takes one argument: table, a table name."
        )
    return table.strip()


class EventSubscriptions:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        can_read_table: TableAccess,
        send: Sender = send_signed,
        clock: Clock = _utcnow,
    ) -> None:
        self._uow_factory = uow_factory
        self._can_read_table = can_read_table
        self._send = send
        self._clock = clock

    async def subscribe(
        self, principal: McpPrincipal | None, request: SubscribeRequest
    ) -> dict[str, JsonValue]:
        reader = _require_reader(principal)
        await self._validate(reader, request)
        _, arguments_key = canonical_arguments(request.arguments)
        public_id = subscription_id(
            reader.grant_id, request.url, request.name, arguments_key
        )
        now = self._clock()
        async with self._uow_factory() as uow:
            repository = EventSubscriptionRepository(uow)
            existing = await repository.get(public_id)
            if existing is None and (
                await repository.count_for_grant(reader.grant_id)
                >= MAX_SUBSCRIPTIONS_PER_GRANT
            ):
                raise EventSubscriptionError(
                    RESOURCE_EXHAUSTED,
                    "This connection already has as many subscriptions as it may.",
                    {"limit": "subscriptions_per_connection"},
                )
            verified = await repository.url_verified(reader.grant_id, request.url)
        if not verified:
            await self._verify_callback(request, public_id)
        async with self._uow_factory() as uow:
            stored = await EventSubscriptionRepository(uow).upsert(
                public_id=public_id,
                grant_id=reader.grant_id,
                user_id=reader.user_id,
                pod_id=reader.pod_id,
                name=request.name,
                arguments=dict(request.arguments),
                arguments_key=arguments_key,
                url=request.url,
                secret_ciphertext=str(get_secret_cipher().encrypt_str(request.secret)),
                refresh_before=now + granted_ttl(request.ttl_ms),
                verified_at=now,
                now=now,
            )
            await uow.commit()
        return {
            "id": stored.public_id,
            "refreshBefore": _iso(stored.refresh_before),
            "cursor": None,
            "truncated": False,
            "deliveryStatus": {
                "active": True,
                "lastDeliveryAt": _iso(stored.last_delivery_at),
                "lastError": stored.last_error,
            },
        }

    async def unsubscribe(
        self,
        principal: McpPrincipal | None,
        *,
        name: str,
        arguments: dict[str, JsonValue],
        url: str,
    ) -> dict[str, JsonValue]:
        """Idempotent, and scoped to the connection by construction: the id is
        derived from this grant, so another connection's subscription with the
        same name, arguments and URL is a different row."""
        reader = _require_reader(principal)
        _, arguments_key = canonical_arguments(arguments)
        async with self._uow_factory() as uow:
            await EventSubscriptionRepository(uow).remove(
                subscription_id(reader.grant_id, url, name, arguments_key)
            )
            await uow.commit()
        return {}

    async def _validate(self, reader: McpPrincipal, request: SubscribeRequest) -> None:
        if request.mode != "webhook":
            raise EventSubscriptionError(
                UNSUPPORTED, "Only webhook delivery is offered.", {"mode": request.mode}
            )
        if request.name not in EVENT_NAMES:
            raise EventSubscriptionError(
                NOT_FOUND, "No such event.", {"kind": "event", "name": request.name}
            )
        if not valid_secret(request.secret):
            raise EventSubscriptionError(
                INVALID_PARAMS,
                "delivery.secret must be whsec_ and the base64 of 24 to 64 bytes.",
            )
        table = _table_argument(request.arguments)
        if not await self._can_read_table(reader.pod_id, reader.user_id, table):
            raise EventSubscriptionError(
                FORBIDDEN, "You cannot read that table.", {"table": table}
            )
        try:
            await assert_safe_url(request.url)
        except UnsafeUrlError as exc:
            raise EventSubscriptionError(
                INVALID_PARAMS,
                "delivery.url must be a public https URL.",
                {"reason": exc.reason},
            ) from exc

    async def _verify_callback(self, request: SubscribeRequest, public_id: str) -> None:
        """Prove the callback is the client's before sending it anything real:
        a signed challenge it must echo back, compared in constant time."""
        challenge = secrets.token_urlsafe(32)
        result = await self._send(
            url=request.url,
            secret=request.secret,
            message_id="msg_verification_" + secrets.token_hex(8),
            subscription_id=public_id,
            # Written out rather than serialized: the challenge is URL-safe
            # base64, so there is nothing in it to escape.
            body=f'{{"type":"verification","challenge":"{challenge}"}}'.encode(),
        )
        reason = _verification_failure(result, challenge)
        if reason is not None:
            raise EventSubscriptionError(
                CALLBACK_ENDPOINT_ERROR,
                "The callback did not verify.",
                {"reason": reason},
            )


def _verification_failure(result: SendResult, challenge: str) -> str | None:
    if result.status is None:
        failure = result.failure or "connection_refused"
        return "timeout" if failure == "timeout" else "connection_refused"
    if 400 <= result.status < 500:
        return "http_4xx"
    if result.status >= 500 or result.status < 200 or result.status >= 300:
        return "http_5xx"
    try:
        echoed = json.loads(result.body or b"{}").get("challenge")
    except ValueError, AttributeError:
        return "challenge_failed"
    if not isinstance(echoed, str) or not hmac.compare_digest(echoed, challenge):
        return "challenge_failed"
    return None


async def can_read_table(pod_id: UUID, user_id: UUID, table: str) -> bool:
    """The production `TableAccess`: the table, read as the person."""
    from app.core.authorization.factory import create_authorization_data_service
    from app.core.domain.errors import DomainError
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.datastore.contracts.provisioning import get_table

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        try:
            ctx = await create_authorization_data_service(uow).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            return await get_table(uow, pod_id=pod_id, name=table, ctx=ctx) is not None
        except DomainError:
            return False
