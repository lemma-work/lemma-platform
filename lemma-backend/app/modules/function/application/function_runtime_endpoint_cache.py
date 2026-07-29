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


RuntimeEndpointLoader = Callable[[], Awaitable[FunctionRuntimeEndpoint]]


@dataclass(frozen=True, slots=True)
class _CachedEndpoint:
    endpoint: FunctionRuntimeEndpoint
    valid_until: float


class FunctionRuntimeEndpointCache:
    """Short-lived, single-flight cache for a pod's resident runtime endpoint.

    The endpoint is a stable, API-key-authenticated AgentBox route. Cache refresh
    verifies the exact provider allocation and resident runtime; ordinary calls
    reuse the route and let AgentBox extend activity while proxying the request.
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
        wait_until: datetime | None = None,
        loader: RuntimeEndpointLoader,
    ) -> FunctionRuntimeEndpoint:
        self._validate_deadline(wait_until)
        for attempt in range(2):
            now = self._clock()
            async with self._lock:
                cached = self._take_cached(key, now)
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
            return endpoint
        raise AssertionError("unreachable runtime endpoint cache retry state")

    def _take_cached(
        self,
        key: FunctionRuntimeEndpointKey,
        now: float,
    ) -> FunctionRuntimeEndpoint | None:
        cached = self._entries.get(key)
        if cached is None:
            return None
        if cached.valid_until > now:
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
            async with self._lock:
                self._entries[key] = _CachedEndpoint(
                    endpoint=endpoint,
                    valid_until=self._clock() + self._ttl_seconds,
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
    def _validate_deadline(wait_until: datetime | None) -> None:
        if wait_until is not None and (
            wait_until.tzinfo is None or wait_until.utcoffset() is None
        ):
            raise ValueError("endpoint wait deadline must include a timezone")
