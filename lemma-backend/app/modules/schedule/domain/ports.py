"""What a schedule's triage needs from outside the module, said in its own terms."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.domain.triage import TriageRoute, TriageVerdict


class ScheduleEventTriage(Protocol):
    """Decide what happens to one event, without exposing how it is decided.

    Asked once per event, under the same subject a filter files its decision
    under, by the event's owner: the row's owner on an RLS table, otherwise the
    schedule's. `personal` says the event is theirs alone.

    Raises `ScheduleTriageUndecidedError` when nothing settles it, and
    `ScheduleTriageInterruptedError` when a failing rung left it open.
    """

    async def triage_event(
        self,
        *,
        schedule: ScheduleEntity,
        event_payload: Mapping[str, object],
        source_event_id: str,
        owner_id: UUID,
        personal: bool = False,
    ) -> TriageVerdict: ...


@dataclass(frozen=True, slots=True)
class TriageQuestion:
    """A held event put to the person it belongs to."""

    pod_id: UUID
    recipient_user_id: UUID
    schedule_id: UUID
    schedule_name: str | None
    run_id: UUID
    decision_id: UUID
    decider: str
    question: str
    routes: Mapping[str, TriageRoute]
    evidence: str | None


class TriageQuestions(Protocol):
    """Where a held event's question goes, and where its answer comes back from."""

    async def ask(self, question: TriageQuestion) -> UUID | None:
        """Put the question, once per held run; the notification's id, if it could."""
        ...
