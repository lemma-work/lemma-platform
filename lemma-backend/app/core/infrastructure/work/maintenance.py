"""Keeping queue rows honest, arming periodics, and the runtime's own retention.

Queue rows are created by enqueue with ``ON CONFLICT DO NOTHING`` so writers
never lock them. Two consequences are handled here rather than on the hot path:
a cap written when a namespace first had work goes stale when an override
changes, and a queue row deleted by retention while an enqueue was still
uncommitted would leave that item with no row to be claimed through. Both are
repaired on a timer, for namespaces that have queued work.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.work.config import default_max_running, work_settings
from app.core.infrastructure.work.context import FastContext
from app.core.infrastructure.work.limits import WorkLimitPort
from app.core.infrastructure.work.rates import (
    DELETE_FULL_BUCKETS,
    default_rates,
    rates_json,
)
from app.core.infrastructure.work.registry import WorkRegistry, registry
from app.core.infrastructure.work.types import SYSTEM_NAMESPACE, WorkClass, WorkMode
from app.core.infrastructure.work.writer import WorkWriter
from app.core.log.log import get_logger

logger = get_logger(__name__)

_REPAIR_QUEUES = text(
    """
    INSERT INTO work_queues (class, ns, max_running, rates)
    SELECT DISTINCT w.class, w.ns, CAST(:cap AS integer), CAST(:rates AS jsonb)
      FROM work_items w
     WHERE w.state = 'queued' AND w.class = :cls
       AND NOT EXISTS (SELECT 1 FROM work_queues q
                        WHERE q.class = w.class AND q.ns = w.ns)
    ON CONFLICT (class, ns) DO NOTHING
    """
)
_QUEUES_WITH_WORK = text(
    """
    SELECT q.ns, q.max_running, q.rates FROM work_queues q
     WHERE q.class = :cls
       AND EXISTS (SELECT 1 FROM work_items w
                    WHERE w.class = q.class AND w.ns = q.ns AND w.state = 'queued')
    """
)
_SET_LIMITS = text(
    """
    UPDATE work_queues SET max_running = :cap, rates = CAST(:rates AS jsonb)
     WHERE class = :cls AND ns = :ns
    """
)
# Only when nothing for this name is live: a periodic already queued or running
# re-arms itself on completion, and a second chain would double its rate.
_ARM_PERIODIC = text(
    """
    SELECT EXISTS (SELECT 1 FROM work_items
                    WHERE dedup_key = :dedup_key AND state IN ('queued', 'running'))
    """
)
_DEAD_PERIODIC = text(
    "SELECT EXISTS (SELECT 1 FROM work_items WHERE dedup_key = :dedup_key AND state = 'dead')"
)


async def refresh_queues(
    session: AsyncSession, cls: WorkClass, limits: WorkLimitPort
) -> int:
    """Recreate missing queue rows and bring caps and rates in line; returns rows changed."""
    await session.execute(
        _REPAIR_QUEUES,
        {
            "cls": cls.value,
            "cap": default_max_running(cls),
            "rates": rates_json(default_rates()),
        },
    )
    rows = (await session.execute(_QUEUES_WITH_WORK, {"cls": cls.value})).all()
    changed = 0
    for row in rows:
        limits_now = await limits.limits_for(row.ns)
        cap = limits_now.cap(cls)
        rates = rates_json(limits_now.rates)
        # Compared as the stored object rather than by parsing `rates` back:
        # same values, no JSON work per queue on the loop.
        stored = {name: rate.as_json() for name, rate in limits_now.rates.items()}
        if cap != row.max_running or stored != row.rates:
            await session.execute(
                _SET_LIMITS,
                {"cap": cap, "rates": rates, "cls": cls.value, "ns": row.ns},
            )
            changed += 1
    return changed


#: Serializes boot-time arming across processes. Without it two workers
#: booting together both see no live item and both add one: the second insert
#: waits on the first's uncommitted row in the dedup index, and if that row has
#: been claimed (``running``) by the time it rechecks, it no longer conflicts --
#: two chains, each re-arming itself forever. Under the lock the second worker
#: sees the first's item, queued or running, and adds nothing.
_ARM_LOCK = text("SELECT pg_advisory_xact_lock(:key)")
_ARM_LOCK_KEY = 0x776F726B70657231  # "workper1"


async def arm_periodics(
    session: AsyncSession, work_registry: WorkRegistry, classes: list[WorkClass]
) -> int:
    """Boot-time upsert of every periodic whose kind runs in this process.

    Idempotent across processes (see ``_ARM_LOCK``); the caller commits, which
    releases the lock.
    """
    writer = WorkWriter(session, registry=work_registry)
    armed = 0
    await session.execute(_ARM_LOCK, {"key": _ARM_LOCK_KEY})
    for spec in work_registry.periodics():
        handler = work_registry.find(spec.kind)
        if handler is None or handler.cls not in classes:
            continue
        params = {"dedup_key": spec.dedup_key}
        if await session.scalar(_ARM_PERIODIC, params):
            continue
        if await session.scalar(_DEAD_PERIODIC, params):
            logger.warning("work.periodic.recreated", name=spec.name, kind=spec.kind)
        await writer.add(
            spec.kind,
            SYSTEM_NAMESPACE,
            spec.payload,
            dedup_key=spec.dedup_key,
            delay=spec.phase,
        )
        armed += 1
    return armed


class RetentionPayload(BaseModel):
    pass


_DELETE_DEAD = text(
    """
    DELETE FROM work_items WHERE id IN (
        SELECT id FROM work_items
         WHERE state = 'dead' AND run_at < now() - make_interval(days => :days)
         LIMIT :batch)
    """
)
_DELETE_IDLE_QUEUES = text(
    """
    DELETE FROM work_queues q
     WHERE q.last_served < now() - interval '7 days' AND NOT q.paused
       AND NOT EXISTS (SELECT 1 FROM work_items w
                        WHERE w.class = q.class AND w.ns = q.ns)
    """
)
_RETENTION_BATCH = 10_000


@registry.handler(
    kind="work.retention",
    cls=WorkClass.ADMIN,
    mode=WorkMode.FAST,
    payload=RetentionPayload,
)
async def work_retention(ctx: FastContext, payload: RetentionPayload) -> None:
    """Drop dead items past retention, queue rows idle for a week, and full buckets."""
    del payload
    session = ctx.uow.session
    dead = await session.execute(
        _DELETE_DEAD,
        {"days": work_settings.work_dead_retention_days, "batch": _RETENTION_BATCH},
    )
    queues = await session.execute(_DELETE_IDLE_QUEUES)
    buckets = await session.execute(DELETE_FULL_BUCKETS)
    logger.info(
        "work.retention.pruned",
        dead_items=dead.rowcount,  # type: ignore[attr-defined]
        idle_queues=queues.rowcount,  # type: ignore[attr-defined]
        full_buckets=buckets.rowcount,  # type: ignore[attr-defined]
    )


registry.periodic("work.retention", timedelta(days=1), "work.retention")
