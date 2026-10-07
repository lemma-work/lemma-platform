"""Queueing the job that asks a workflow step's decision, after the commit.

The decision executor suspends a run on a DECISION wait; the job that asks the
question has to start only once that wait is committed, or it may look for a
wait that is not there yet -- and a rollback must take the job with it. So the
enqueue is registered on the unit of work and runs after its commit, the way
an approval's reconciliation is.

A lost enqueue does not lose the decision. The wait stays ACTIVE, and the
reconciliation sweep queues the same job again (`DecisionResumeService`).
"""

from __future__ import annotations

from coredis.exceptions import RedisError

from app.core.domain.job_queue import JobQueuePort
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.core.log.log import get_logger

logger = get_logger(__name__)

DECISION_STEP_JOB = "decide_workflow_step"


def decision_step_job_id(external_ref: str) -> str:
    """One job per pending decision: a second enqueue while it is queued,
    retrying or running is dropped by streaq rather than asking twice."""
    return f"workflow-decision:{external_ref}"


async def enqueue_decision_step(external_ref: str, queue: JobQueuePort) -> None:
    try:
        await queue.enqueue(
            DECISION_STEP_JOB,
            external_ref=external_ref,
            _job_id=decision_step_job_id(external_ref),
        )
    except RedisError, OSError, TimeoutError:
        # The run is committed and waiting; raising here would turn the request
        # that started it into an error for a step that will still be asked.
        logger.warning(
            "workflow.decision_queue.enqueue_deferred.degraded",
            external_ref=external_ref,
            exc_info=True,
        )


class AfterCommitDecisionQueue:
    """`DecisionPort` bound to one unit of work."""

    def __init__(
        self, uow: SqlAlchemyUnitOfWork, *, queue: JobQueuePort | None = None
    ) -> None:
        self._uow = uow
        self._queue = queue

    def ask_once_committed(self, external_ref: str) -> None:
        self._uow.after_commit(
            lambda: enqueue_decision_step(external_ref, self._job_queue())
        )

    def _job_queue(self) -> JobQueuePort:
        return self._queue if self._queue is not None else get_streaq_job_queue()
