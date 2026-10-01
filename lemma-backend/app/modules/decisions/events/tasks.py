"""Hourly: drop the evidence of decisions past their correction window.

A decision keeps the input view it was asked about so a person can review and
correct it. Past `DECISION_EVIDENCE_TTL_DAYS` that copy is dropped; the answer
stays. An example made from a correction keeps its own copy, by a person's
choice. Bounded batches, so a sweep that is far behind catches up over a few
runs rather than holding the bulk lane.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.infrastructure.db.session import get_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_runtime import Lane, streaq_cron
from app.core.log.log import get_logger
from app.modules.decisions.infrastructure.repositories import SqlDecisionStore

logger = get_logger(__name__)

SWEEP_BATCH = 1000
SWEEP_BATCHES_PER_RUN = 20


# :40, among the quietest minutes: the worker is single-loop, and :23 already
# carries the datastore, notification and recovery sweeps.
@streaq_cron("40 * * * *", name="forget_expired_decision_evidence", lane=Lane.BULK)
async def forget_expired_decision_evidence() -> None:
    store = SqlDecisionStore(SessionUnitOfWorkFactory(get_session_maker()))
    forgotten = 0
    for _ in range(SWEEP_BATCHES_PER_RUN):
        count = await store.forget_expired_evidence(
            now=datetime.now(timezone.utc), limit=SWEEP_BATCH
        )
        forgotten += count
        if count < SWEEP_BATCH:
            break
    if forgotten:
        logger.info(
            "decisions.tasks.expired_evidence_forgotten.observed", forgotten=forgotten
        )
