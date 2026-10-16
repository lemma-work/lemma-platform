"""TIME schedules as FAST handlers: ``schedule.fire`` and ``schedule.arm``.

A TIME schedule owns one pending ``schedule.fire`` item, due at its cursor.
The handler locks the row only while the cursor still equals the item's
``fire_at`` and the schedule is active: an edit, a pause or a delete since the
item was written changed or removed the cursor, so the item finds nothing and
is done. Otherwise, in one transaction with the item's deletion, it advances
the cursor (collapsing any backlog), writes the next fire item, and fires --
which reserves the run (``cron:<id>:<fire_at>``, unique) and writes the
target's start item. An edit racing a fire either commits first (and the fire
finds nothing) or waits on the row lock and then computes from the fire's new
cursor, so a schedule can never be left with two fire chains.

Database only, as a FAST handler must be; every import is local and none
reaches the network.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.infrastructure.work import DeadContext, FastContext
from app.modules.schedule.work.kinds import FIRE_FAILED, ScheduleRef, TimeFire


async def run_time_fire(ctx: FastContext, payload: TimeFire) -> None:
    from sqlalchemy import select

    from app.modules.schedule.domain.schedule import ScheduleType
    from app.modules.schedule.domain.time_cursor import (
        NO_FUTURE_FIRE,
        coalesced_cursor,
        is_one_shot,
    )
    from app.modules.schedule.infrastructure.models.schedule import Schedule
    from app.modules.schedule.repositories.time_arming import arm_time_fire
    from app.modules.schedule.services.paused_notice import stage_paused_notice
    from app.modules.schedule.services.trigger_fire import TriggerEvent, fire_trigger
    from app.modules.schedule.work.kinds import time_source_event_id

    session = ctx.uow.session
    row = await session.scalar(
        select(Schedule)
        .where(
            Schedule.id == payload.schedule_id,
            Schedule.next_fire_at == payload.fire_at,
            Schedule.is_active.is_(True),
            Schedule.schedule_type == ScheduleType.TIME,
        )
        .with_for_update()
    )
    if row is None:
        return
    schedule = row.to_entity()
    now = datetime.now(timezone.utc)
    upcoming = coalesced_cursor(row.config, fire_at=payload.fire_at, now=now)
    one_shot = is_one_shot(row.config)
    if one_shot or upcoming is None:
        row.next_fire_at = None
        row.is_active = False
        if not one_shot:
            # A cron that can no longer produce a fire has stopped, and nobody
            # asked it to: say so. A one-shot that just ran is simply done.
            row.paused_reason = NO_FUTURE_FIRE
            await stage_paused_notice(
                ctx.uow,
                schedule_id=row.id,
                user_id=row.user_id,
                pod_id=row.pod_id,
                schedule_type=row.schedule_type,
                reason=NO_FUTURE_FIRE,
                work=ctx.work,
            )
    else:
        row.next_fire_at = upcoming
        await arm_time_fire(
            session,
            schedule_id=row.id,
            pod_id=row.pod_id,
            fire_at=upcoming,
            work=ctx.work,
        )
    await session.flush()
    await fire_trigger(
        ctx.uow,
        schedule,
        TriggerEvent(
            source_event_id=time_source_event_id(row.id, payload.fire_at),
            payload=dict((schedule.config or {}).get("payload") or {}),
            source_occurred_at=payload.fire_at.astimezone(timezone.utc),
        ),
        work=ctx.work,
    )


async def run_arm(ctx: FastContext, payload: ScheduleRef) -> None:
    """Give an active TIME schedule a cursor if it has none, and its fire item.

    For rows the write paths did not arm: ones written before the work
    runtime, armed by its migration. A config that can never fire is retired here,
    saying why, rather than left active and silent.
    """
    from sqlalchemy import select

    from app.modules.schedule.domain.schedule import ScheduleType
    from app.modules.schedule.domain.time_cursor import initial_cursor
    from app.modules.schedule.infrastructure.models.schedule import Schedule
    from app.modules.schedule.repositories.time_arming import arm_time_fire
    from app.modules.schedule.services.paused_notice import stage_paused_notice

    session = ctx.uow.session
    row = await session.scalar(
        select(Schedule)
        .where(
            Schedule.id == payload.schedule_id,
            Schedule.is_active.is_(True),
            Schedule.schedule_type == ScheduleType.TIME,
        )
        .with_for_update()
    )
    if row is None:
        return
    if row.next_fire_at is None:
        cursor, retire_reason = initial_cursor(
            row.config, now=datetime.now(timezone.utc)
        )
        if retire_reason is not None:
            row.is_active = False
            row.paused_reason = retire_reason
            await stage_paused_notice(
                ctx.uow,
                schedule_id=row.id,
                user_id=row.user_id,
                pod_id=row.pod_id,
                schedule_type=row.schedule_type,
                reason=retire_reason,
                work=ctx.work,
            )
            await session.flush()
            return
        row.next_fire_at = cursor
        await session.flush()
    assert row.next_fire_at is not None
    await arm_time_fire(
        session,
        schedule_id=row.id,
        pod_id=row.pod_id,
        fire_at=row.next_fire_at,
        work=ctx.work,
    )


async def on_fire_dead(ctx: DeadContext) -> None:
    """A fire out of tries leaves its schedule with no fire item: nothing
    would ever run it again, and nothing would say so. It is paused instead,
    as ``fire_failed``, and its owner is told -- reactivating re-arms it."""
    from sqlalchemy import select

    from app.modules.schedule.domain.schedule import ScheduleType
    from app.modules.schedule.infrastructure.models.schedule import Schedule
    from app.modules.schedule.repositories.schedule_repository import (
        ScheduleRepository,
    )
    from app.modules.schedule.services.paused_notice import stage_paused_notice

    try:
        payload = TimeFire.model_validate(ctx.item.payload)
    except ValueError:
        return
    row = await ctx.uow.session.scalar(
        select(Schedule)
        .where(
            Schedule.id == payload.schedule_id,
            # Only the chain this item was: an edit since armed another.
            Schedule.next_fire_at == payload.fire_at,
            Schedule.is_active.is_(True),
            Schedule.schedule_type == ScheduleType.TIME,
        )
        .with_for_update()
    )
    if row is None:
        return
    if not await ScheduleRepository(uow=ctx.uow).pause(row.id, reason=FIRE_FAILED):
        return
    await stage_paused_notice(
        ctx.uow,
        schedule_id=row.id,
        user_id=row.user_id,
        pod_id=row.pod_id,
        schedule_type=row.schedule_type,
        reason=FIRE_FAILED,
        work=ctx.work,
    )
