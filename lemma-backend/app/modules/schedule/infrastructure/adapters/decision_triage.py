"""`ScheduleEventTriage`, answered by the schedule's pod decider.

The triage's decider is asked about the event through the decisions contract,
exactly as a filter's inline question is: under the same subject, so a retry
or a redelivery reads the recorded decision instead of asking again; by the
same asker, the event's owner, PERSONAL when the event is a row on an RLS
table. Its answer is routed by the schedule's `routes`, and an `act` past the
hour's `act_per_hour` goes to the digest, or to a person when there is none.
An act is admitted -- its place in the hour taken -- before its fire is
published, so a burst of events cannot all find the hour empty.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.modules.decisions.contracts.decide import (
    Asker,
    DeciderNotFoundError,
    DecisionEntity,
    Lane,
    decide,
)
from app.modules.pod.contracts.detached_reads import pod_organization_id_detached
from app.modules.schedule.domain.errors import (
    ScheduleTriageDeciderMissingError,
    ScheduleTriageInterruptedError,
    ScheduleTriageUndecidedError,
)
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.domain.triage import (
    TriageConfig,
    TriageRoute,
    TriageVerdict,
    verdict_output,
)
from app.modules.schedule.infrastructure.adapters.system_model_filter import (
    OrganizationLookup,
    filter_subject,
    json_object,
)
from app.modules.schedule.repositories.act_admissions import admit_act

#: The window `act_per_hour` is counted over.
ACT_WINDOW = timedelta(hours=1)


class DecideNamed(Protocol):
    """The decisions contract's `decide`, asking a pod decider by name."""

    async def __call__(
        self,
        *,
        state: JsonValue,
        asker: Asker,
        decider: str,
        subject: str,
        lane: Lane,
    ) -> DecisionEntity: ...


class ActAdmission(Protocol):
    """Whether an event may act now, taking one of the window's `ceiling` places."""

    async def __call__(
        self, schedule_id: UUID, source_event_id: str, *, ceiling: int
    ) -> bool: ...


class StoredActAdmission:
    """`ActAdmission` kept in Postgres, one short unit of work per event."""

    def __init__(self, uow_factory: UnitOfWorkFactory | None = None) -> None:
        self._uow_factory = uow_factory or SessionUnitOfWorkFactory(async_session_maker)

    async def __call__(
        self, schedule_id: UUID, source_event_id: str, *, ceiling: int
    ) -> bool:
        async with self._uow_factory() as uow:
            return await admit_act(
                uow.session,
                schedule_id=schedule_id,
                source_event_id=source_event_id,
                ceiling=ceiling,
                window=ACT_WINDOW,
                now=datetime.now(timezone.utc),
            )


class DecisionScheduleTriage:
    """Ask the schedule's decider about an event, and route what it answers."""

    def __init__(
        self,
        *,
        decide_named: DecideNamed | None = None,
        organization_of: OrganizationLookup | None = None,
        admit_act: ActAdmission | None = None,
    ) -> None:
        self._decide: DecideNamed = decide_named or decide
        self._organization_of: OrganizationLookup = (
            organization_of or pod_organization_id_detached
        )
        self._admit_act: ActAdmission = admit_act or StoredActAdmission()

    async def triage_event(
        self,
        *,
        schedule: ScheduleEntity,
        event_payload: Mapping[str, object],
        source_event_id: str,
        owner_id: UUID,
        personal: bool = False,
    ) -> TriageVerdict:
        triage = schedule.triage
        if triage is None:
            raise ValueError("this schedule has no triage")
        decision = await self._decision_about(
            schedule,
            triage,
            event_payload=event_payload,
            source_event_id=source_event_id,
            owner_id=owner_id,
            personal=personal,
        )
        question = triage.question or _only_question(decision)
        answer, fallback = answer_of(decision, question, triage)
        route = triage.routes[answer]
        routed_from: TriageRoute | None = None
        if route is TriageRoute.ACT and not await self._admitted(
            schedule.id, source_event_id, triage
        ):
            routed_from, route = route, triage.over_ceiling
        return TriageVerdict(
            route=route,
            decision_id=decision.id,
            question=question,
            answer=answer,
            evidence=decision.evidence,
            output=verdict_output(
                decision_id=decision.id,
                question=question,
                answer=answer,
                route=route,
                routed_from=routed_from,
                fallback=fallback,
            ),
        )

    async def _decision_about(
        self,
        schedule: ScheduleEntity,
        triage: TriageConfig,
        *,
        event_payload: Mapping[str, object],
        source_event_id: str,
        owner_id: UUID,
        personal: bool,
    ) -> DecisionEntity:
        organization_id = (
            await self._organization_of(schedule.pod_id)
            if schedule.pod_id is not None
            else None
        )
        asker = Asker(
            user_id=owner_id,
            pod_id=schedule.pod_id,
            organization_id=organization_id,
            # The decision keeps the event as its evidence. A row on an RLS
            # table is its owner's alone, so the record of judging it is too.
            visibility="PERSONAL" if personal else schedule.visibility,
        )
        try:
            return await self._decide(
                state=json_object(event_payload),
                asker=asker,
                decider=triage.decider,
                subject=filter_subject(schedule, source_event_id),
                # Nobody is watching an event being sorted, whatever lane the
                # decider would choose for itself.
                lane=Lane.AMBIENT,
            )
        except DeciderNotFoundError as exc:
            raise ScheduleTriageDeciderMissingError() from exc

    async def _admitted(
        self, schedule_id: UUID, source_event_id: str, triage: TriageConfig
    ) -> bool:
        if triage.act_per_hour is None:
            return True
        return await self._admit_act(
            schedule_id, source_event_id, ceiling=triage.act_per_hour
        )


def answer_of(
    decision: DecisionEntity, question: str, triage: TriageConfig
) -> tuple[str, bool]:
    """The option an event is routed by, and whether it is the question's fallback.

    An open question follows the filter's rules: left open by a failing rung,
    it is asked again; left open because nothing committed, it fails -- unless
    the question's fallback option is one the routes cover, which is what a
    fallback is for. An answer the routes do not name fails too: the decider
    was changed under the schedule.
    """
    answer = decision.answers.get(question)
    value = answer.value if answer is not None else None
    is_open = question in decision.open
    if is_open and decision.interrupted:
        raise ScheduleTriageInterruptedError(decision.id)
    if not isinstance(value, str) or value not in triage.routes:
        raise ScheduleTriageUndecidedError(decision.id)
    return value, is_open


def _only_question(decision: DecisionEntity) -> str:
    if len(decision.shape) != 1:
        raise ScheduleTriageUndecidedError(decision.id)
    return next(iter(decision.shape))
