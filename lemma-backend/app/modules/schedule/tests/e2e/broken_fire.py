"""A ``schedule.fire`` handler that always fails, for the dead-fire e2e test.

Its own module because a FAST handler's module may import no network client,
and the test module that uses it imports httpx.
"""

from __future__ import annotations

from app.core.infrastructure.work import FastContext
from app.modules.schedule.work.kinds import TimeFire


async def broken_time_fire(ctx: FastContext, payload: TimeFire) -> None:
    raise RuntimeError(f"the fire for {payload.schedule_id} broke")
