"""Drive the runtime deterministically from a test: no background loops, no sleeps.

``run_until_idle`` runs the same claim, execute and reap code a worker runs, in
rounds, until a round finds nothing due. Items scheduled in the future are not
due; move them with ``make_due`` when a test wants them now.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.infrastructure.work.dispatcher import Dispatcher
from app.core.infrastructure.work.db import SessionMaker, WorkDb
from app.core.infrastructure.work.execute import ExecEnv
from app.core.infrastructure.work.registry import (
    WorkRegistry,
    registry as default_registry,
)
from app.core.infrastructure.work.types import WorkClass


def dispatcher_for(
    session_maker: SessionMaker,
    cls: WorkClass,
    *,
    registry: WorkRegistry | None = None,
    owner: str = "test",
    slots: int = 32,
    quantum: int = 2,
    statement_timeout_ms: int = 2000,
) -> Dispatcher:
    env = ExecEnv(
        db=WorkDb(session_maker, None),
        registry=registry or default_registry,
        owner=f"{owner}:{cls.value}",
        statement_timeout_ms=statement_timeout_ms,
    )
    return Dispatcher(cls, env, slots=slots, quantum=quantum, poll_interval=0.05)


async def run_until_idle(
    session_maker: SessionMaker,
    *,
    registry: WorkRegistry | None = None,
    classes: list[WorkClass] | None = None,
    max_rounds: int = 1_000,
    slots: int = 32,
) -> int:
    """Run due work of ``classes`` until none is left; returns items executed.

    ``slots`` per class: a test pool smaller than ``slots`` sessions (plus the
    side connections their writers open) needs fewer.

    Each round reaps expired leases, claims into every free slot and awaits
    what it claimed, so follow-up work enqueued by a handler runs in a later
    round. Raises if ``max_rounds`` pass without going idle -- a handler that
    re-enqueues itself forever is a bug the test should see.
    """
    dispatchers = [
        dispatcher_for(session_maker, cls, registry=registry, slots=slots)
        for cls in (classes or list(WorkClass))
    ]
    executed = 0
    for _ in range(max_rounds):
        claimed = 0
        for dispatcher in dispatchers:
            await dispatcher.reap()
            items = await dispatcher.claim_once()
            claimed += len(items)
            await asyncio.gather(*(dispatcher.execute(item) for item in items))
        if claimed == 0:
            return executed
        executed += claimed
    raise RuntimeError(f"work did not go idle within {max_rounds} rounds")


drain = run_until_idle


async def make_due(session_maker: SessionMaker, *, kind: str | None = None) -> int:
    """Pull every queued item (of ``kind``) forward to now."""
    async with session_maker() as session:
        result = await session.execute(
            text(
                "UPDATE work_items SET run_at = now() "
                "WHERE state = 'queued' AND (CAST(:kind AS text) IS NULL OR kind = :kind)"
            ),
            {"kind": kind},
        )
        await session.commit()
    return int(result.rowcount)  # type: ignore[attr-defined]
