"""Renewable ownership for identity operations that cross service boundaries."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from typing import AsyncIterator

from redis.asyncio import Redis
from redis.asyncio.lock import Lock

from app.core.infrastructure.redis.client import get_redis


class IdentityLeaseLost(RuntimeError):
    pass


class IdentityLease:
    def __init__(self, lock: Lock) -> None:
        self._lock = lock
        self._stopped = asyncio.Event()

    async def require_ownership(self) -> None:
        if not await self._lock.owned():
            raise IdentityLeaseLost("Identity operation ownership was lost; retry")

    async def renew(self) -> None:
        while not self._stopped.is_set():
            try:
                async with asyncio.timeout(10):
                    await self._stopped.wait()
            except TimeoutError:
                await self._lock.extend(30, replace_ttl=True)

    def stop(self) -> None:
        self._stopped.set()


@asynccontextmanager
async def identity_lease(
    key: str, *, redis: Redis | None = None
) -> AsyncIterator[IdentityLease]:
    client = redis if redis is not None else get_redis()
    digest = hashlib.sha256(key.encode()).hexdigest()
    lock = client.lock(
        f"identity:operation:{digest}",
        timeout=30,
        blocking_timeout=10,
        thread_local=False,
    )
    if not await lock.acquire():
        raise IdentityLeaseLost("Identity operation is busy; retry")
    lease = IdentityLease(lock)
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(lease.renew())
            try:
                yield lease
                await lease.require_ownership()
            finally:
                lease.stop()
    except BaseExceptionGroup as failures:
        if len(failures.exceptions) == 1:
            raise failures.exceptions[0] from failures
        raise
    finally:
        if await lock.owned():
            await lock.release()
