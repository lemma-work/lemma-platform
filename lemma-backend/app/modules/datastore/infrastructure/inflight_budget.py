"""Aggregate in-flight byte budget for document extraction.

``document_processing_max_concurrency`` caps the *number* of concurrent
extractions but not their combined size, so a couple of large documents can
still stack to an OOM. This gate bounds the total document bytes held in memory
across all concurrent extractions: a task reserves its file's ``size_bytes``
before extracting and releases after, blocking when the running total would
exceed the configured limit.

It is a soft complement to the count semaphore, not a replacement:
- ``limit <= 0`` disables it (the default) so it is a no-op unless a deployment
  opts in — zero behaviour change on rollout.
- A single file larger than the whole budget is still allowed through when
  nothing else is running, so a lone large file can't deadlock (it is instead
  bounded by ``document_processing_max_file_bytes``).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.modules.datastore.config import datastore_settings


class InFlightByteBudget:
    def __init__(self, limit_bytes: int) -> None:
        self._limit = limit_bytes
        self._in_flight = 0
        self._cond = asyncio.Condition()

    @asynccontextmanager
    async def reserve(self, nbytes: int) -> AsyncIterator[None]:
        if self._limit <= 0:
            yield
            return
        nbytes = max(0, int(nbytes or 0))
        async with self._cond:
            # Wait for room, unless nothing else is in flight (then allow this
            # item through even if it alone exceeds the limit, to avoid a stall).
            while self._in_flight > 0 and self._in_flight + nbytes > self._limit:
                await self._cond.wait()
            self._in_flight += nbytes
        try:
            yield
        finally:
            async with self._cond:
                self._in_flight -= nbytes
                self._cond.notify_all()


_budget: InFlightByteBudget | None = None


def get_inflight_byte_budget() -> InFlightByteBudget:
    global _budget
    if _budget is None:
        _budget = InFlightByteBudget(
            datastore_settings.document_processing_max_inflight_bytes
        )
    return _budget


def reset_inflight_byte_budget() -> None:
    """Test hook: drop the process-wide budget so it rebuilds from settings."""
    global _budget
    _budget = None
