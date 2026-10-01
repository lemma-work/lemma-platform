"""Settling an event a schedule held for its person, once the question closes.

The person's answer is routed by the options they were shown, which the
question carries (`TriageAsk`): act fires the held event now, digest keeps it
held for the next digest, ignore ends it as skipped. Their answer is recorded
on the decision too, as a person's, which is what teaches the decider. A
question that expires or is cancelled unanswered ends the event as skipped:
nothing that waited for a person runs without one.

At most once, whatever is redelivered: every way out of a held event is a
compare-and-set on its `held_for`, so a second answer finds nothing to move.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleRunEntity
from app.modules.schedule.domain.triage import TriageAsk, TriageRoute
from app.modules.schedule.repositories.held_runs import HeldRunRepository
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ClosedQuestion:
    """How a held event's question closed: what it asked, and who chose what.

    `answer` is None when nobody did -- the question expired or was cancelled,
    which `status` says.
    """

    pod_id: UUID
    ask: TriageAsk
    status: str
    answer: str | None = None
    responder_user_id: UUID | None = None


class TeachDecision(Protocol):
    """Record a person's answer on a decision, so it becomes an example."""

    async def __call__(
        self,
        *,
        decision_id: UUID,
        pod_id: UUID,
        question: str,
        answer: str,
        user_id: UUID,
    ) -> None: ...


async def teach_decision(
    *, decision_id: UUID, pod_id: UUID, question: str, answer: str, user_id: UUID
) -> None:
    """`answer` the decision as `user_id`, unless they already have.

    The check makes a redelivery a no-op rather than a second example: the
    decisions contract records every answer it is given.
    """
    from app.modules.decisions.contracts.decide import (
        Rung,
        answer as record,
        get_decision,
    )

    decision = await get_decision(
        decision_id=decision_id, pod_id=pod_id, viewer_id=user_id
    )
    given = decision.answers.get(question)
    if given is not None and given.by is Rung.PERSON and given.value == answer:
        return
    await record(
        decision_id=decision_id,
        pod_id=pod_id,
        answers={question: answer},
        by=Rung.PERSON,
        user_id=user_id,
    )


class HeldAnswerRuns(Protocol):
    """The held runs an answer settles: `HeldRunRepository`, as much as is used."""

    async def get(self, run_id: UUID) -> ScheduleRunEntity | None: ...

    async def settle_ask(
        self,
        run_id: UUID,
        *,
        route: TriageRoute,
        llm_output: Mapping[str, JsonValue],
        now: datetime,
    ) -> ScheduleRunEntity | None: ...


class ScheduleReader(Protocol):
    async def get(self, schedule_id: UUID) -> ScheduleEntity | None: ...


class TriageAnswers:
    """Settle held events by their person's answer."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        teach: TeachDecision | None = None,
        held_runs: Callable[[SqlAlchemyUnitOfWork], HeldAnswerRuns] | None = None,
        schedules: Callable[[SqlAlchemyUnitOfWork], ScheduleReader] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._teach: TeachDecision = teach or teach_decision
        self._held_runs = held_runs or HeldRunRepository
        self._schedules = schedules or ScheduleRepository

    async def settle(self, closed: ClosedQuestion) -> TriageRoute | None:
        """Where the held event went, or None when there was nothing left to move."""
        async with self._uow_factory() as uow:
            run = await self._held_runs(uow).get(closed.ask.run_id)
        if run is None or not run.awaiting_answer:
            return None
        route, output = await self._chosen(closed, run)
        async with self._uow_factory() as uow:
            schedule = await self._schedules(uow).get(run.schedule_id)
            if schedule is None:
                return None
            if route is TriageRoute.DIGEST and schedule.next_digest_at is None:
                # Nothing would ever send it: the digest was removed since the
                # question went out. The person wanted it seen, so it is now.
                route = TriageRoute.ACT
                output = {**output, "route": route.value, "routed_from": "digest"}
            settled = await self._held_runs(uow).settle_ask(
                run.id, route=route, llm_output=output, now=datetime.now(timezone.utc)
            )
            if settled is None:
                return None
            if route is TriageRoute.ACT:
                uow.collect_events([_fired(schedule, settled)])
        return route

    async def _chosen(
        self, closed: ClosedQuestion, run: ScheduleRunEntity
    ) -> tuple[TriageRoute, dict[str, JsonValue]]:
        """The route the person chose and the run's `llm_output` once they have."""
        output: dict[str, JsonValue] = {**run.llm_output}
        answer, user_id = closed.answer, closed.responder_user_id
        route = closed.ask.route_for(answer) if answer else None
        if answer is None or route is None or user_id is None:
            return TriageRoute.IGNORE, {
                **output,
                "route": TriageRoute.IGNORE.value,
                "unanswered": closed.status,
            }
        await self._record_answer(closed, answer=answer, user_id=user_id)
        return route, {
            **output,
            "answer": answer,
            "route": route.value,
            "answered_by": "person",
        }

    async def _record_answer(
        self, closed: ClosedQuestion, *, answer: str, user_id: UUID
    ) -> None:
        """Teach the decider, without letting a refusal strand the event.

        A decision deleted or no longer visible to its person refuses the
        answer; their choice still decides the event, which is what they were
        asked. A database failure is not a refusal and goes back for retry.
        """
        try:
            await self._teach(
                decision_id=closed.ask.decision_id,
                pod_id=closed.pod_id,
                question=closed.ask.question,
                answer=answer,
                user_id=user_id,
            )
        except DomainError:
            logger.warning(
                "schedule.triage_answers.answer_not_recorded.degraded",
                decision_id=str(closed.ask.decision_id),
                run_id=str(closed.ask.run_id),
                exc_info=True,
            )


def _fired(schedule: ScheduleEntity, run: ScheduleRunEntity) -> ScheduleFired:
    """The held event's fire, as a redrive re-fires a run: its own payload and key."""
    return ScheduleFired(
        schedule_id=schedule.id,
        user_id=run.user_id,
        schedule_type=schedule.schedule_type,
        pod_id=schedule.pod_id,
        account_id=schedule.account_id,
        payload=run.payload,
        metadata=run.metadata,
        llm_output=run.llm_output,
        scheduled_at=run.source_occurred_at,
        source_event_id=run.source_event_id,
        causation_id=run.id,
    )
