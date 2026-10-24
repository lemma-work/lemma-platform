"""Reading what a pod owns into its week: ``datastore.outcome_week``.

One item reads one pod's measure and keeps it as the week's reading, which is
how the pod's history comes to exist at all: a measure answers only now. Two
things write it. The ``PodOutcomeChanged`` fact, in the transaction that set
or changed what the pod owns, so a new measure has its first week straight
away; and the sweep in ``outcome_week_sweep``, on an interval, for every pod
with a measure, so each week ends on a reading taken near its end.

Keyed per pod, so a reading already queued absorbs the next one asked for,
and run one at a time per pod, so two readings of one pod never race.
A JOB in the admin class: it runs a query on the datastore database, and a
sweep's worth of them must never queue ahead of record changes or people.
Idempotent by construction -- a second run reads again and keeps the later
reading.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.work import (
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.pod.contracts.outcome import PodOutcomeChanged

OUTCOME_WEEK = "datastore.outcome_week"


class OutcomeWeekPayload(BaseModel):
    pod_id: UUID


def outcome_week_key(pod_id: UUID) -> str:
    """Both the dedup and the concurrency key of a pod's reading."""
    return f"outcome-week:{pod_id}"


async def read_outcome_week(ctx: JobContext, payload: OutcomeWeekPayload) -> None:
    from app.modules.datastore.services.wiring import build_outcome_standing_service

    # Read in a short transaction of its own -- the measure runs on the
    # datastore database, bounded by the query timeout -- and keep what was
    # read in the completion, which commits with the item's deletion.
    async with ctx.checkpoint() as uow:
        reading = await build_outcome_standing_service(uow).read_week(payload.pod_id)
    if reading is None:
        return
    async with ctx.complete_work() as uow:
        await build_outcome_standing_service(uow).keep_week(reading)


def register_outcome_week_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=OUTCOME_WEEK,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=OutcomeWeekPayload,
        concurrency=1,
    )(read_outcome_week)


register_outcome_week_work(default_registry)
facts.subscribe(
    PodOutcomeChanged,
    kind=OUTCOME_WEEK,
    cls=WorkClass.ADMIN,
    dedup=lambda fact: outcome_week_key(fact.pod_id),
    concurrency_key=lambda fact: outcome_week_key(fact.pod_id),
)
