from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from uuid import UUID

from opentelemetry import trace

from app.core.request_context import create_inherited_task

tracer = trace.get_tracer(__name__)


@dataclass(frozen=True, slots=True)
class FunctionRuntimeEndpointKey:
    pod_id: UUID
    profile_digest: str


@dataclass(frozen=True, slots=True)
class FunctionRuntimeEndpoint:
    url: str
    request_headers: tuple[tuple[str, str], ...] = field(repr=False)
    allocation_id: UUID
    allocation_epoch: int
    profile_digest: str
    expires_at: datetime

    def headers(self) -> dict[str, str]:
        return dict(self.request_headers)


RuntimeEndpointLoader = Callable[[], Awaitable[FunctionRuntimeEndpoint]]


@dataclass(frozen=True, slots=True)
class _CachedEndpoint:
    endpoint: FunctionRuntimeEndpoint
    valid_until: float


class FunctionRuntimeEndpointCache:
    """Single-flight cache for allocation-bound direct runtime leases."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 30,
        max_entries: int = 4096,
        refresh_headroom_seconds: float = 30,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("function runtime endpoint TTL must be positive")
        if max_entries < 1:
            raise ValueError("function runtime endpoint cache must retain an entry")
        if refresh_headroom_seconds < 0:
            raise ValueError("function runtime endpoint headroom cannot be negative")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._refresh_headroom_seconds = refresh_headroom_seconds
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
        self._validate_deadline(wait_until)
        self._validate_deadline(required_valid_until)
        # A miss here costs two control-plane calls (ensure + lease), so whether
        # this hits is the difference between a warm dispatch and a slow one.
        # It was previously unobservable, which made the reuse window impossible
        # to reason about from production data.
        with tracer.start_as_current_span("lemma.function.runtime_endpoint") as span:
            span.set_attribute("lemma.pod_id", str(key.pod_id))
            return await self._get(
                key,
                span=span,
                required_valid_until=required_valid_until,
                wait_until=wait_until,
                loader=loader,
            )

    async def _get(
        self,
        key: FunctionRuntimeEndpointKey,
        *,
        span: trace.Span,
        required_valid_until: datetime,
        wait_until: datetime | None,
        loader: RuntimeEndpointLoader,
    ) -> FunctionRuntimeEndpoint:
        for attempt in range(2):
            now = self._clock()
            async with self._lock:
                cached = self._take_cached(
                    key,
                    now,
                    required_valid_until=required_valid_until,
                )
                if cached is not None:
                    span.set_attribute("lemma.cache", "hit")
                    return cached
                task = self._inflight.get(key)
                joined = task is not None
                span.set_attribute("lemma.cache", "joined" if joined else "miss")
                if task is None:
                    task = create_inherited_task(
                        self._load(key, loader=loader),
                        name=f"function-runtime-endpoint:{key.pod_id}",
                    )
                    self._inflight[key] = task

            try:
                endpoint, failure = await self._wait_for_endpoint(task, wait_until)
            except TimeoutError:
                # Give up on this attempt *and* on the work behind it.
                #
                # The task outlives the caller by design -- it carries its own,
                # much longer deadline -- so abandoning it without cancelling
                # left it issuing guest calls for minutes after nobody wanted
                # the answer, while still sitting in `_inflight`. The next
                # request then *joined* that doomed task and inherited its
                # remaining wait, which is why one slow start turned into a run
                # of identical two-minute failures instead of one.
                await self._stop_new_joiners(key, task)
                raise
            if failure is not None:
                if joined and attempt == 0:
                    continue
                raise failure
            assert endpoint is not None
            if not self._valid_for(endpoint, key, required_valid_until):
                await self.invalidate(key, endpoint=endpoint)
                if joined and attempt == 0:
                    continue
                raise ValueError(
                    "function runtime endpoint lease is shorter than the caller deadline"
                )
            return endpoint
        raise AssertionError("unreachable runtime endpoint cache retry state")

    async def _stop_new_joiners(self, key, task) -> None:
        """Take a load that outlived its caller out of the join table.

        Evicted, not cancelled. Another caller may still be waiting on it with
        a longer deadline of its own -- cancelling would fail them for someone
        else's timeout -- and if nobody is, letting it finish leaves a warm
        entry for the next request rather than throwing the work away.

        What must not continue is *joining*: a request arriving after this one
        gave up used to attach to the same task and inherit whatever was left
        of its wait, so one slow start became a run of identical two-minute
        failures instead of a single one.
        """
        async with self._lock:
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)

    def _take_cached(
        self,
        key: FunctionRuntimeEndpointKey,
        now: float,
        *,
        required_valid_until: datetime,
    ) -> FunctionRuntimeEndpoint | None:
        cached = self._entries.get(key)
        if cached is None:
            return None
        if cached.valid_until > now and self._valid_for(
            cached.endpoint,
            key,
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
            if not self._valid_for(endpoint, key, self._wall_clock()):
                raise ValueError(
                    "function runtime endpoint lease is already expired or mismatched"
                )
            seconds_to_expiry = (
                endpoint.expires_at - self._wall_clock()
            ).total_seconds()
            cache_seconds = min(
                self._ttl_seconds,
                seconds_to_expiry - self._refresh_headroom_seconds,
            )
            if cache_seconds <= 0:
                raise ValueError(
                    "function runtime endpoint lease has insufficient cache lifetime"
                )
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
    def _validate_deadline(wait_until: datetime | None) -> None:
        if wait_until is not None and (
            wait_until.tzinfo is None or wait_until.utcoffset() is None
        ):
            raise ValueError("endpoint wait deadline must include a timezone")

    @staticmethod
    def _valid_for(
        endpoint: FunctionRuntimeEndpoint,
        key: FunctionRuntimeEndpointKey,
        required_valid_until: datetime,
    ) -> bool:
        return (
            endpoint.profile_digest == key.profile_digest
            and endpoint.allocation_epoch >= 1
            and endpoint.expires_at.tzinfo is not None
            and endpoint.expires_at.utcoffset() is not None
            and endpoint.expires_at >= required_valid_until
        )
