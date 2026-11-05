"""The daily ceiling on a trigger's fires, and what happens at it.

Fires per hour are a rate (``trigger_fire`` on ``schedule.start_target``):
excess fires wait. Fires per day are a ceiling: a trigger past it is almost
certainly looping -- a workflow writing the table that triggers it, a webhook
storm -- so it pauses itself (``paused_reason = fire_cap``), the owner is told,
and what it would have fired meanwhile is recorded as ``fire_cap`` skips.

The ceiling is the ``trigger_fire_day`` limit, read from the namespace's queue
row, where the limit port writes every namespace's rates -- so a pod or org
override reaches it through the same seam as every other cap, with no port
call (and no network) on the fire path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter
from app.core.infrastructure.work.rates import default_rates, rates_from_json
from app.core.log.log import get_logger
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.repositories.fire_ledger import FireLedger
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.repositories.trigger_records import TriggerRecords
from app.modules.schedule.services.paused_notice import stage_paused_notice
from app.modules.schedule.work.kinds import schedule_namespace

logger = get_logger(__name__)

FIRE_CAP = "fire_cap"
DAILY_RATE = "trigger_fire_day"
_WINDOW = timedelta(days=1)

_STORED_RATES = text(
    "SELECT rates FROM work_queues WHERE ns = :ns AND rates ? :name LIMIT 1"
)


async def daily_ceiling(session: AsyncSession, ns: str) -> int:
    stored = await session.scalar(_STORED_RATES, {"ns": ns, "name": DAILY_RATE})
    rate = (rates_from_json(stored) if stored is not None else default_rates()).get(
        DAILY_RATE
    )
    return rate.limit if rate is not None else 0


async def fires_left_today(
    uow: SqlAlchemyUnitOfWork, schedule: ScheduleEntity
) -> int | None:
    """How many more fires the ceiling allows in the last day; ``None``: no ceiling."""
    ceiling = await daily_ceiling(uow.session, schedule_namespace(schedule.pod_id))
    if ceiling <= 0:
        return None
    since = datetime.now(timezone.utc) - _WINDOW
    fired = await FireLedger(uow.session).fires_since(
        schedule.id, since=since, limit=ceiling
    )
    return max(0, ceiling - fired)


async def at_daily_ceiling(uow: SqlAlchemyUnitOfWork, schedule: ScheduleEntity) -> bool:
    return await fires_left_today(uow, schedule) == 0


async def pause_at_fire_cap(
    uow: SqlAlchemyUnitOfWork,
    schedule: ScheduleEntity,
    *,
    source_event_id: str,
    work: WorkWriter,
) -> None:
    """Pause once, tell the owner once, and record the fire that hit the cap."""
    await TriggerRecords(uow.session).record_skips(
        schedule.id,
        reason=FIRE_CAP,
        first=source_event_id,
        last=source_event_id,
        count=1,
    )
    if not await ScheduleRepository(uow=uow).pause(schedule.id, reason=FIRE_CAP):
        return
    logger.warning(
        "schedule.fire_cap.paused",
        schedule_id=str(schedule.id),
        pod_id=str(schedule.pod_id) if schedule.pod_id else None,
    )
    await stage_paused_notice(
        uow,
        schedule_id=schedule.id,
        user_id=schedule.user_id,
        pod_id=schedule.pod_id,
        schedule_type=schedule.schedule_type,
        reason=FIRE_CAP,
        work=work,
    )


__all__ = [
    "FIRE_CAP",
    "at_daily_ceiling",
    "daily_ceiling",
    "fires_left_today",
    "pause_at_fire_cap",
]
