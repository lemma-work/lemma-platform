"""Asking about a held event, once its hold is on record.

Both event paths -- the webhook task and the DATASTORE handler -- hold an
event the same way and ask about it the same way, and the order is the point:
the HELD row commits first, then the person is asked. A question put first
and a hold that then failed would leave an answer with nothing to route; the
other way round, a redelivery finds the row still waiting and asks again,
which the question's own idempotency key makes a no-op.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.schedule.domain.ports import TriageQuestion, TriageQuestions
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleRunEntity
from app.modules.schedule.domain.triage import TriageVerdict


async def ask_if_waiting(
    questions: TriageQuestions,
    schedule: ScheduleEntity,
    run: ScheduleRunEntity | None,
    verdict: TriageVerdict,
) -> UUID | None:
    """Ask the run's person about it while it waits for an answer; else nothing.

    The run is read as it stands: a redelivered event whose first delivery
    was already answered is not asked about again.
    """
    if (
        run is None
        or not run.awaiting_answer
        or run.user_id is None
        or schedule.triage is None
        or schedule.pod_id is None
    ):
        return None
    return await questions.ask(
        TriageQuestion(
            pod_id=schedule.pod_id,
            # Whose event it is: the row's owner on an RLS table, who alone may
            # see what was judged, and the schedule's owner otherwise.
            recipient_user_id=run.user_id,
            schedule_id=schedule.id,
            schedule_name=schedule.name,
            run_id=run.id,
            decision_id=verdict.decision_id,
            decider=schedule.triage.decider,
            question=verdict.question,
            routes=schedule.triage.routes,
            evidence=verdict.evidence,
        )
    )
