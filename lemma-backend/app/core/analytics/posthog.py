"""PostHog transport for the analytics sink.

Deliberately not the ``posthog`` package. The capture API is one JSON POST, the
repo already owns a pooled outbound client, and a hand-rolled adapter keeps the
dependency surface flat and the ClickStack swap symmetric — that sink will be
the same shape against a different endpoint.

``capture`` is synchronous and never blocks: it appends to a bounded buffer and
returns. A background task drains the buffer in batches. Analytics must never
add latency to a request or fail one, so every failure mode here ends in
dropped events and a counter, never an exception reaching the caller.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone

import httpx

from app.core.analytics.sink import CapturedEvent
from app.core.log.log import get_logger
from app.core.net.http_client import get_shared_http_client
from app.core.request_context import create_background_task

logger = get_logger(__name__)

#: Bounded so a sink that cannot reach PostHog costs memory that is capped
#: rather than memory that grows until the process dies. Oldest events are
#: dropped first: during an incident the recent ones are the interesting ones.
DEFAULT_BUFFER_LIMIT = 10_000
DEFAULT_BATCH_SIZE = 250
DEFAULT_FLUSH_INTERVAL_SECONDS = 5.0

#: Per-phase, so ``pool`` stays separable. A bare float would set all four
#: phases, and the pool phase is the one that matters: the shared client
#: (``app/core/net/http_client.py``) gives request-path callers a 5s pool
#: budget, and analytics waiting longer than that would let a flush outrank a
#: connector execution for the last free connection. 1s is deliberately *below*
#: the shared budget -- under pool pressure analytics yields, the batch fails,
#: and ``_post`` drops it. That is the correct priority ordering.
_POST_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=1.0)

#: The final drain at shutdown is bounded, because an unreachable endpoint with
#: a full buffer is 40 sequential posts and would hang teardown for minutes.
#: The repo budgets 5s for a whole shutdown step, and analytics is one step of
#: many, so this sits comfortably inside it.
_FINAL_DRAIN_TIMEOUT_SECONDS = 2.0

#: How long the flusher gets to notice ``_stopping`` and return on its own.
_TASK_STOP_TIMEOUT_SECONDS = 5.0


class PostHogSink:
    def __init__(
        self,
        *,
        write_key: str,
        host: str,
        buffer_limit: int = DEFAULT_BUFFER_LIMIT,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
    ) -> None:
        #: Public so ``bootstrap.start_analytics`` can recognise an already-live
        #: sink for the same key and decline to build a second one.
        self.write_key = write_key
        self._endpoint = f"{host.rstrip('/')}/batch/"
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._buffer: deque[dict] = deque(maxlen=buffer_limit)
        self._dropped = 0
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    # -- producer side -----------------------------------------------------

    def capture(self, event: CapturedEvent) -> None:
        properties = dict(event.properties)
        if event.groups:
            # Group analytics: PostHog reads memberships off this reserved key.
            properties["$groups"] = dict(event.groups)
        if len(self._buffer) == self._buffer.maxlen:
            self._dropped += 1
        self._buffer.append(
            {
                "event": event.name,
                "distinct_id": event.distinct_id,
                "properties": properties,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    # -- consumer side -----------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            # A long-lived flusher must not inherit the request context of
            # whichever request happened to start it.
            self._task = create_background_task(self._run(), name="analytics-flush")

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._flush_interval
                )
            except asyncio.TimeoutError:
                pass
            try:
                await self._drain_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the flusher must outlive one bad drain
                # Without this the task dies on the first escape and every
                # later event is silently dropped for the life of the process,
                # with one overflow log at shutdown to show for it.
                logger.warning(
                    "analytics.flush.failed", error_type=type(exc).__name__
                )

    async def _drain_once(self) -> None:
        while self._buffer:
            batch = [
                self._buffer.popleft()
                for _ in range(min(self._batch_size, len(self._buffer)))
            ]
            await self._post(batch)

    async def _post(self, batch: list[dict]) -> None:
        try:
            client = get_shared_http_client()
            response = await client.post(
                self._endpoint,
                json={"api_key": self.write_key, "batch": batch},
                timeout=_POST_TIMEOUT,
            )
            if response.status_code >= 400:
                # Dropped, not retried. A retry queue here would mean analytics
                # owning durability, and the domain outbox already owns that
                # for anything that matters.
                logger.warning(
                    "analytics.delivery.failed",
                    status=response.status_code,
                    count=len(batch),
                )
        except Exception as exc:  # noqa: BLE001 - delivery must never propagate
            logger.warning(
                "analytics.delivery.failed",
                error_type=type(exc).__name__,
                count=len(batch),
            )

    async def aclose(self) -> None:
        """Stop the flusher, deliver what is buffered, and give up on time.

        Bounded at every step. An unreachable endpoint must cost a deployment a
        couple of seconds at shutdown, never a hung pod. ``CancelledError`` is
        deliberately not caught: it belongs to whoever is shutting us down, and
        swallowing it would let teardown continue past its own cancellation.
        """
        if self._task is None and not self._buffer:
            return
        self._stopping.set()
        task, self._task = self._task, None
        if task is not None:
            try:
                # On timeout ``wait_for`` cancels the task and awaits it, so the
                # flusher is never still inside ``_post`` when the final drain
                # below starts popping the same deque.
                await asyncio.wait_for(task, timeout=_TASK_STOP_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                pass
            except Exception as exc:  # noqa: BLE001 - a dead flusher still owes us a drain
                logger.warning(
                    "analytics.flush.failed", error_type=type(exc).__name__
                )
        try:
            await asyncio.wait_for(
                self._drain_once(), timeout=_FINAL_DRAIN_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            logger.warning(
                "analytics.shutdown.drain_timed_out", count=len(self._buffer)
            )
        if self._dropped:
            logger.warning("analytics.buffer.overflowed", count=self._dropped)
            self._dropped = 0
