from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from uuid import UUID

from app.core.request_context import create_inherited_task


@dataclass(frozen=True, slots=True)
class FunctionRuntimeEndpointKey:
    pod_id: UUID
    profile_digest: str


@dataclass(frozen=True, slots=True)
class FunctionRuntimeEndpoint:
    url: str
    expires_at: datetime


RuntimeEndpointLoader = Callable[[], Awaitable[FunctionRuntimeEndpoint]]


@dataclass(frozen=True, slots=True)
class _CachedEndpoint:
    endpoint: FunctionRuntimeEndpoint
    valid_until: float


class FunctionRuntimeEndpointCache:
    """Short-lived, single-flight cache for a pod's resident runtime endpoint.

    Cache refresh calls AgentBox ``ensure`` and port access, which also refreshes
    AgentBox's idle accounting. Invocation requests themselves never pass through
    the manager, so the TTL must remain well below the five-minute sandbox idle
    timeout.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 30,
        max_entries: int = 4096,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("function runtime endpoint TTL must be positive")
        if max_entries < 1:
            raise ValueError("function runtime endpoint cache must retain an entry")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._wall_clock = wall_clock
        self._entries: OrderedDict[FunctionRuntimeEndpointKey, _CachedEndpoint] = (
            OrderedDict()
        )
        self._inflight: dict[
            FunctionRuntimeEndpointKey,
            asyncio.Task[FunctionRuntimeEndpoint],
        ] = {}
        self._lock = asyncio.Lock()

    async def get(
        self,
        key: FunctionRuntimeEndpointKey,
        *,
        required_valid_until: datetime,
        wait_until: datetime | None = None,
        loader: RuntimeEndpointLoader,
    ) -> FunctionRuntimeEndpoint:
        self._validate_deadlines(required_valid_until, wait_until)
        for attempt in range(2):
            now = self._clock()
            async with self._lock:
                cached = self._take_cached(key, now, required_valid_until)
                if cached is not None:
                    return cached
                task = self._inflight.get(key)
                joined = task is not None
                if task is None:
                    task = create_inherited_task(
                        self._load(key, loader=loader),
                        name=f"function-runtime-endpoint:{key.pod_id}",
                    )
                    self._inflight[key] = task

            endpoint, failure = await self._wait_for_endpoint(task, wait_until)
            if failure is not None:
                if joined and attempt == 0:
                    continue
                raise failure
            assert endpoint is not None
            if self._covers(endpoint, required_valid_until):
                return endpoint
            await self.invalidate(key, endpoint=endpoint)
        raise TimeoutError(
            "function runtime grant does not cover the invocation deadline"
        )

    def _take_cached(
        self,
        key: FunctionRuntimeEndpointKey,
        now: float,
        required_valid_until: datetime,
    ) -> FunctionRuntimeEndpoint | None:
        cached = self._entries.get(key)
        if cached is None:
            return None
        if cached.valid_until > now and self._covers(
            cached.endpoint,
            required_valid_until,
        ):
            self._entries.move_to_end(key)
            return cached.endpoint
        self._entries.pop(key, None)
        return None

    async def _wait_for_endpoint(
        self,
        task: asyncio.Task[FunctionRuntimeEndpoint],
        wait_until: datetime | None,
    ) -> tuple[FunctionRuntimeEndpoint | None, BaseException | None]:
        timeout = (
            None
            if wait_until is None
            else max(0.0, (wait_until - self._wall_clock()).total_seconds())
        )
        completed, _ = await asyncio.wait((task,), timeout=timeout)
        if not completed:
            raise TimeoutError(
                "function runtime endpoint was not ready before the caller deadline"
            )
        if task.cancelled():
            raise asyncio.CancelledError
        failure = task.exception()
        if failure is not None:
            return None, failure
        return task.result(), None
        raise AssertionError("unreachable runtime endpoint cache retry state")

    async def invalidate(
        self,
        key: FunctionRuntimeEndpointKey,
        *,
        endpoint: FunctionRuntimeEndpoint | None = None,
    ) -> None:
        async with self._lock:
            cached = self._entries.get(key)
            if cached is None:
                return
            if endpoint is None or cached.endpoint == endpoint:
                self._entries.pop(key, None)

    async def _load(
        self,
        key: FunctionRuntimeEndpointKey,
        *,
        loader: RuntimeEndpointLoader,
    ) -> FunctionRuntimeEndpoint:
        task = asyncio.current_task()
        try:
            endpoint = await loader()
            grant_remaining = (
                endpoint.expires_at - self._wall_clock()
            ).total_seconds()
            # Never serve a grant at its expiry boundary. Very short grants still
            # satisfy the caller but are deliberately not cached.
            cache_seconds = min(self._ttl_seconds, max(0.0, grant_remaining - 2))
            if cache_seconds <= 0:
                return endpoint
            async with self._lock:
                self._entries[key] = _CachedEndpoint(
                    endpoint=endpoint,
                    valid_until=self._clock() + cache_seconds,
                )
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
            return endpoint
        finally:
            async with self._lock:
                if self._inflight.get(key) is task:
                    self._inflight.pop(key, None)

    @staticmethod
    def _covers(
        endpoint: FunctionRuntimeEndpoint, required_valid_until: datetime
    ) -> bool:
        return endpoint.expires_at >= required_valid_until

    @staticmethod
    def _validate_deadlines(
        required_valid_until: datetime,
        wait_until: datetime | None,
    ) -> None:
        if (
            required_valid_until.tzinfo is None
            or required_valid_until.utcoffset() is None
        ):
            raise ValueError("required endpoint lifetime must include a timezone")
        if wait_until is not None and (
            wait_until.tzinfo is None or wait_until.utcoffset() is None
        ):
            raise ValueError("endpoint wait deadline must include a timezone")
