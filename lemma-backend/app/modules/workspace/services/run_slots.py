"""Run slots: how many agent runs share a pod machine at once.

A pod machine has ``run_slots`` of them. A run takes one on its first command
and holds it, renewing the lease with every command, until it ends; a run that
finds none free waits a little and is then told the machine is busy, rather
than piling a sixth review onto a disk sized for two. A lease that is not
renewed lapses, so a run that died without ending still frees its slot.

Kept in Redis -- a sorted set per machine, scored by lease expiry -- because
every API replica and worker hands out the same slots.
"""

from __future__ import annotations

import asyncio
import time
from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis
from app.modules.workspace.domain.machine_errors import MachineConflict

_SLOTS = "machine:slots:v1:{machine_id}"
_RUN = "machine:run-machines:v1:{run_id}"
#: How long a slot is held without a command renewing it. Longer than any one
#: command's wait, shorter than an abandoned run should block a machine for.
LEASE_SECONDS = 30 * 60
#: How long a run waits for a slot before being told the machine is busy.
WAIT_SECONDS = 60.0
_POLL_SECONDS = 2.0


class RunSlots:
    def __init__(self) -> None:
        self._redis = get_redis(url=settings.redis_url)

    async def acquire(
        self,
        machine_id: UUID,
        run_id: UUID,
        *,
        slots: int,
        wait_seconds: float = WAIT_SECONDS,
    ) -> None:
        key = _SLOTS.format(machine_id=machine_id)
        deadline = time.monotonic() + wait_seconds
        while True:
            now = time.time()
            await self._redis.zremrangebyscore(key, "-inf", now)
            held = await self._redis.zscore(key, str(run_id))
            if held is not None or await self._redis.zcard(key) < slots:
                await self._redis.zadd(key, {str(run_id): now + LEASE_SECONDS})
                await self._redis.expire(key, LEASE_SECONDS * 2)
                await self._remember(run_id, machine_id)
                return
            if time.monotonic() >= deadline:
                raise MachineConflict(
                    f"This machine is busy: all {slots} of its run slots are in "
                    "use. Try again shortly, or ask a pod editor for more slots.",
                    code="MACHINE_BUSY",
                )
            await asyncio.sleep(_POLL_SECONDS)

    async def release(self, machine_id: UUID, run_id: UUID) -> None:
        await self._redis.zrem(_SLOTS.format(machine_id=machine_id), str(run_id))

    async def holders(self, machine_id: UUID) -> list[str]:
        key = _SLOTS.format(machine_id=machine_id)
        await self._redis.zremrangebyscore(key, "-inf", time.time())
        members = await self._redis.zrange(key, 0, -1)
        return [m if isinstance(m, str) else bytes(m).decode() for m in members]

    async def machines_of(self, run_id: UUID) -> list[UUID]:
        members = await self._redis.smembers(_RUN.format(run_id=run_id))
        found: list[UUID] = []
        for member in members or ():
            value = member if isinstance(member, str) else bytes(member).decode()
            try:
                found.append(UUID(value))
            except ValueError:
                continue
        return found

    async def forget_run(self, run_id: UUID) -> None:
        await self._redis.delete(_RUN.format(run_id=run_id))

    async def _remember(self, run_id: UUID, machine_id: UUID) -> None:
        key = _RUN.format(run_id=run_id)
        await self._redis.sadd(key, str(machine_id))
        await self._redis.expire(key, LEASE_SECONDS * 4)
