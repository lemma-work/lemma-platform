"""Keeping MCP event subscriptions and the schedules they were made for in step.

A subscription is made on its author's account and renewed by the connectors
module on its own clock. Nothing there knows about schedules, so three things
went unnoticed:

- **Orphans.** A schedule deleted, or a create whose request rolled back after
  subscribing, left a subscription renewing on someone's credentials forever.
- **An author who left the pod.** Their account kept listening and the
  schedule kept firing, on authority they no longer had.
- **A subscription that stopped renewing.** The account was disconnected, or
  the server started refusing. The schedule still read "When issue.created
  happens" and never fired again, silently.

This pass finds each. An orphan is unsubscribed. The other two turn the
schedule off and say why, the way PS-SCHED-023 asks: the subscription is
dropped and the routing key cleared, so turning it back on subscribes afresh
on the author's account (`resubscribe_on_reactivation`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.connectors.contracts.mcp_events import McpListening
from app.modules.schedule.domain.events.schedule import ScheduleDeactivated
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.repositories.listening_queries import (
    schedules_listening_through,
)
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository

logger = get_logger(__name__)

#: A subscription younger than this may belong to a create still in flight,
#: whose schedule row is not committed yet.
ORPHAN_GRACE = timedelta(minutes=15)

#: Renewals failed in a row, past the server's `refreshBefore`, before the
#: schedule is turned off. A short outage is retried, with backoff, without
#: anyone being told; this is the point it has stopped being short.
LAPSED_AFTER_FAILURES = 3

PAGE = 200

AUTHOR_LEFT = "MCP_AUTHOR_LEFT_POD"
LAPSED = "MCP_SUBSCRIPTION_LAPSED"

#: What the schedule says about why it is off, where its author will look.
WHY = {
    AUTHOR_LEFT: (
        "Turned off: the person whose account it listened through is no longer "
        "in this space."
    ),
    LAPSED: (
        "Turned off: the server stopped accepting its subscription. Reconnect "
        "the account if it needs signing in again, then turn this back on."
    ),
}

ListeningPage = Callable[..., Awaitable[list[McpListening]]]
Unsubscribe = Callable[[str], Awaitable[None]]
#: Whether this person is in this pod, now.
IsMember = Callable[[object, UUID, UUID], Awaitable[bool]]


async def _is_member(uow: object, pod_id: UUID, user_id: UUID) -> bool:
    from app.modules.pod.contracts.members import pod_member_id

    return await pod_member_id(uow, pod_id, user_id) is not None


@dataclass
class Reconciled:
    orphans: int = 0
    turned_off: int = 0


class McpListeningReconciler:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        page: ListeningPage,
        unsubscribe: Unsubscribe,
        is_member: IsMember = _is_member,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._uow_factory = uow_factory
        self._page = page
        self._unsubscribe = unsubscribe
        self._is_member = is_member
        self._clock = clock

    async def run(self) -> Reconciled:
        now = self._clock()
        done = Reconciled()
        after: str | None = None
        while True:
            states = await self._page(after=after, limit=PAGE, now=now)
            if not states:
                break
            await self._reconcile(states, now, done)
            if len(states) < PAGE:
                break
            after = states[-1].subscription_id
        if done.orphans or done.turned_off:
            logger.info(
                "schedule.mcp_listening.reconciled",
                orphan_count=done.orphans,
                turned_off_count=done.turned_off,
            )
        return done

    async def _reconcile(
        self, states: list[McpListening], now: datetime, done: Reconciled
    ) -> None:
        async with self._uow_factory() as uow:
            schedules = await schedules_listening_through(
                uow.session, [state.subscription_id for state in states]
            )
        for state in states:
            schedule = schedules.get(state.subscription_id)
            if schedule is None:
                if (
                    state.created_at is not None
                    and state.created_at < now - ORPHAN_GRACE
                ):
                    await self._unsubscribe(state.subscription_id)
                    done.orphans += 1
                continue
            if schedule.pod_id is None:
                continue
            # A paused schedule is reconciled too: its subscription keeps
            # renewing on the author's account while it is paused, so an
            # author who has left would otherwise keep listening until
            # somebody resumed it.
            reason = await self._reason_to_stop(schedule, state)
            if reason is not None:
                await self._turn_off(schedule, state, reason)
                done.turned_off += 1

    async def _reason_to_stop(
        self, schedule: ScheduleEntity, state: McpListening
    ) -> str | None:
        async with self._uow_factory() as uow:
            if not await self._is_member(uow, schedule.pod_id, schedule.user_id):
                return AUTHOR_LEFT
        if state.lapsed and state.renew_failures >= LAPSED_AFTER_FAILURES:
            return LAPSED
        return None

    async def _turn_off(
        self, schedule: ScheduleEntity, state: McpListening, reason: str
    ) -> None:
        """Off, routing key cleared, and said -- committed together -- and only
        then unsubscribed, so a failed commit leaves the schedule as it was.

        A schedule somebody had already paused is not announced again: nothing
        it was doing has stopped. Its reason is still written where its author
        will look, and resuming it subscribes afresh."""
        config = {
            key: value
            for key, value in schedule.config.items()
            if key != "provider_trigger_id"
        }
        async with self._uow_factory() as uow:
            await ScheduleRepository(uow=uow).update(
                schedule.id,
                is_active=False,
                config=config,
                last_error=WHY[reason]
                + (f" ({state.last_error})" if state.last_error else ""),
            )
            if schedule.is_active:
                uow.collect_events(
                    [
                        ScheduleDeactivated(
                            schedule_id=schedule.id,
                            user_id=schedule.user_id,
                            pod_id=schedule.pod_id,
                            schedule_type=schedule.schedule_type,
                            consecutive_failures=state.renew_failures,
                            reason=reason,
                        )
                    ]
                )
            await uow.commit()
        logger.info(
            "schedule.mcp_listening.turned_off",
            schedule_id=str(schedule.id),
            reason=reason,
        )
        await self._unsubscribe(state.subscription_id)
