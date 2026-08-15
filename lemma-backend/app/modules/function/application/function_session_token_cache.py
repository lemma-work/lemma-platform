from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import time
from uuid import UUID

from opentelemetry import trace

from app.core.request_context import create_inherited_task

tracer = trace.get_tracer(__name__)


@dataclass(frozen=True, slots=True)
class FunctionSessionToken:
    value: str
    expires_at: datetime


FunctionTokenMinter = Callable[..., Awaitable[FunctionSessionToken]]


@dataclass(frozen=True, slots=True)
class FunctionSessionTokenKey:
    user_id: UUID
    pod_id: UUID
    function_id: UUID
    revision_hash: str
    workload_name: str
    scope: tuple[str, ...]
    delegated_tokens_enabled: bool

    @property
    def session_id(self) -> str:
        material = "\0".join(
            (
                str(self.user_id),
                str(self.pod_id),
                str(self.function_id),
                self.revision_hash,
                self.workload_name,
                ",".join(self.scope),
                str(self.delegated_tokens_enabled),
            )
        )
        digest = hashlib.sha256(material.encode()).hexdigest()
        return f"function-session:{digest}"


@dataclass(frozen=True, slots=True)
class _CachedToken:
    token: FunctionSessionToken
    cache_expires_at: float


class FunctionSessionTokenCache:
    """Short-lived, single-flight cache for delegated function sessions."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 300,
        max_entries: int = 4096,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("function session token TTL must be positive")
        if max_entries < 1:
            raise ValueError("function session token cache must retain an entry")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._wall_clock = wall_clock
        self._entries: OrderedDict[FunctionSessionTokenKey, _CachedToken] = (
            OrderedDict()
        )
        self._inflight: dict[
            FunctionSessionTokenKey, asyncio.Task[FunctionSessionToken]
        ] = {}
        self._lock = asyncio.Lock()

    async def get(
        self,
        key: FunctionSessionTokenKey,
        *,
        minter: FunctionTokenMinter,
        min_validity_until: datetime | None = None,
    ) -> FunctionSessionToken:
        # A miss mints through the identity provider -- a user lookup plus a
        # session create, both over HTTP. Recorded because reasoning about this
        # from the outside requires knowing the cached token's own expiry, which
        # comes from the issuer and is not otherwise visible.
        with tracer.start_as_current_span("lemma.function.session_token") as span:
            return await self._get(
                key, span=span, minter=minter, min_validity_until=min_validity_until
            )

    async def _get(
        self,
        key: FunctionSessionTokenKey,
        *,
        span: trace.Span,
        minter: FunctionTokenMinter,
        min_validity_until: datetime | None,
    ) -> FunctionSessionToken:
        now = self._clock()
        required_until = min_validity_until or self._wall_clock()
        async with self._lock:
            cached = self._entries.get(key)
            if (
                cached is not None
                and cached.cache_expires_at > now
                and cached.token.expires_at > required_until
            ):
                self._entries.move_to_end(key)
                span.set_attribute("lemma.cache", "hit")
                return cached.token
            if cached is not None:
                # Distinguishes the two reasons a present entry is unusable: the
                # local TTL lapsing, or the issuer's own expiry not covering the
                # caller's window. They have different fixes.
                span.set_attribute(
                    "lemma.cache",
                    "expired_ttl"
                    if cached.cache_expires_at <= now
                    else "expired_validity",
                )
                self._entries.pop(key, None)
            else:
                span.set_attribute("lemma.cache", "miss")
            task = self._inflight.get(key)
            span.set_attribute("lemma.minting", task is None)
            if task is None:
                task = create_inherited_task(self._mint(key, minter=minter))
                self._inflight[key] = task

        try:
            token = await asyncio.shield(task)
            if token.expires_at <= required_until:
                raise ValueError(
                    "fresh function token expires before the required execution window"
                )
            return token
        finally:
            if task.done():
                async with self._lock:
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key, None)

    async def _mint(
        self,
        key: FunctionSessionTokenKey,
        *,
        minter: FunctionTokenMinter,
    ) -> FunctionSessionToken:
        token = await minter(
            user_id=key.user_id,
            workload_type="function",
            workload_id=key.function_id,
            pod_id=key.pod_id,
            session_id=key.session_id,
            workload_name=key.workload_name,
            scope=list(key.scope) or None,
            delegated_tokens_enabled=key.delegated_tokens_enabled,
        )
        async with self._lock:
            self._entries[key] = _CachedToken(
                token=token,
                cache_expires_at=self._clock() + self._ttl_seconds,
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return token
