"""Work kinds the real-worker test registers in the worker subprocess.

Kept apart from the entrypoint, which also registers a streaq task: a module
holding a FAST handler may not import the job runtime (see ``fast_guard``).
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel
from sqlalchemy import text

from app.core.infrastructure.work import (
    FastContext,
    JobContext,
    PermanentFailure,
    WorkClass,
    WorkMode,
    periodic,
    work_handler,
)


class Empty(BaseModel):
    pass


async def _effect(session: object, tag: str) -> None:
    await session.execute(  # type: ignore[attr-defined]
        text("INSERT INTO work_e2e_effects (tag) VALUES (:tag)"), {"tag": tag}
    )


@work_handler(
    kind="e2e.work.long_job",
    cls=WorkClass.AUTOMATION,
    mode=WorkMode.JOB,
    payload=Empty,
    lease=timedelta(seconds=3),
)
async def long_job(ctx: JobContext, payload: Empty) -> None:
    """Idles until shutdown, then stops at its safe point."""
    async with ctx.checkpoint() as uow:
        await _effect(uow.session, "job.started")
    await ctx.until_stopping()
    ctx.raise_if_stopping()


@work_handler(
    kind="e2e.work.tick", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Empty
)
async def tick(ctx: FastContext, payload: Empty) -> None:
    await _effect(ctx.uow.session, "tick")


@work_handler(
    kind="e2e.work.poison", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Empty
)
async def poison(ctx: FastContext, payload: Empty) -> None:
    raise PermanentFailure("never valid")


periodic("e2e.work.tick", timedelta(seconds=1), "e2e.work.tick")
