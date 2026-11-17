"""Run slots: how many agent runs share a pod machine at once.

A pod machine has ``run_slots`` of them. A run takes one on its first command
and holds it, renewing the lease with every command, until it ends. A run
that finds none free polls for up to a minute and is then told the machine is
busy -- there is no queue, and the refusal says so rather than promising a
place in one. A lease that is not renewed lapses, so a run that died without
ending still frees its slot; a conversation parked WAITING on its own work
keeps its slot on a longer lease (`park`), since that work is running there.

Kept in Redis -- a sorted set per machine, scored by lease expiry -- because
every API replica and worker hands out the same slots. Taking one is a single
script: expire, check the count, add. Reading the count and adding in two
round trips let two runs both see the last free slot and both take it.
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
#: How long a parked (WAITING) conversation keeps its slot without a command.
#: Long enough for the build it waits on; a conversation that never resumes
#: still lets the slot go in the end.
PARKED_LEASE_SECONDS = 24 * 3600
#: How long a run polls for a slot before being told the machine is busy.
WAIT_SECONDS = 60.0
_POLL_SECONDS = 2.0

#: KEYS[1] the machine's slots. ARGV: now, run, lease end, slots, key TTL.
#: 1 when the run holds a slot now, 0 when all are taken by others.
_TAKE_SLOT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
local held = redis.call('ZSCORE', KEYS[1], ARGV[2])
if held or redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[4]) then
  redis.call('ZADD', KEYS[1], ARGV[3], ARGV[2])
  local ttl = redis.call('TTL', KEYS[1])
  if ttl < tonumber(ARGV[5]) then redis.call('EXPIRE', KEYS[1], ARGV[5]) end
  return 1
end
return 0
"""


class RunSlots:
    def __init__(self, *, redis=None) -> None:
        self._redis = redis or get_redis(url=settings.redis_url)

    async def _take(self, key: str, run_id: UUID, *, slots: int) -> bool:
        now = time.time()
        taken = await self._redis.eval(
            _TAKE_SLOT,
            1,
            key,
            now,
            str(run_id),
            now + LEASE_SECONDS,
            slots,
            LEASE_SECONDS * 2,
        )
        return bool(int(taken))

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
            if await self._take(key, run_id, slots=slots):
                await self._remember(run_id, machine_id)
                return
            if time.monotonic() >= deadline:
                raise MachineConflict(
                    f"This machine is busy: all {slots} of its run slots stayed "
                    f"in use for the {wait_seconds:.0f} seconds this run waited, "
                    "and there is no queue to wait in. Run again later, use "
                    "another machine, or ask a pod editor for more slots.",
                    code="MACHINE_BUSY",
                )
            await asyncio.sleep(_POLL_SECONDS)

    async def park(self, run_id: UUID) -> None:
        """A conversation parked WAITING on its own work keeps its slots: the
        work it waits on is running there, and the run that picks it up must
        find the same slot. Its next command renews the ordinary lease."""
        until = time.time() + PARKED_LEASE_SECONDS
        for machine_id in await self.machines_of(run_id):
            key = _SLOTS.format(machine_id=machine_id)
            # XX: only a slot it still holds -- parking must not take one.
            await self._redis.zadd(key, {str(run_id): until}, xx=True)
            if await self._redis.ttl(key) < PARKED_LEASE_SECONDS:
                await self._redis.expire(key, PARKED_LEASE_SECONDS * 2)
        run_key = _RUN.format(run_id=run_id)
        await self._redis.expire(run_key, PARKED_LEASE_SECONDS * 2)

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
