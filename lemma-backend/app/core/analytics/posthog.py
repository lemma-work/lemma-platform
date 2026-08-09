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
        self._write_key = write_key
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
            await self._drain_once()

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
                json={"api_key": self._write_key, "batch": batch},
                timeout=10.0,
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
        self._stopping.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        await self._drain_once()
        if self._dropped:
            logger.warning("analytics.buffer.overflowed", count=self._dropped)
