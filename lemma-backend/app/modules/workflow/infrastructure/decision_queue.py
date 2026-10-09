"""Queueing the job that asks a workflow step's decision, after the commit.

The decision executor suspends a run on a DECISION wait; the job that asks the
question has to start only once that wait is committed, or it may look for a
wait that is not there yet -- and a rollback must take the job with it. So the
enqueue is registered on the unit of work and runs after its commit, the way
an approval's reconciliation is.

A lost enqueue does not lose the decision. The wait stays ACTIVE, and the
reconciliation sweep asks whether the job is still alive and, if it is not,
queues it again (`DecisionResumeService`).
"""

from __future__ import annotations

from typing import Protocol

from coredis.exceptions import RedisError
from streaq.task import TaskStatus

from app.core.domain.job_queue import JobQueuePort
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.core.log.log import get_logger
from app.modules.workflow.domain.ports import DecisionJobState

logger = get_logger(__name__)

DECISION_STEP_JOB = "decide_workflow_step"
#: A job in one of these states will still ask its decision: waiting its turn,
#: waiting out a retry's backoff, or asking now.
_ALIVE = frozenset({TaskStatus.QUEUED, TaskStatus.SCHEDULED, TaskStatus.RUNNING})


class DecisionJobQueue(JobQueuePort, Protocol):
    """A job queue that can also say where a job is."""

    async def status(self, job_id: str) -> TaskStatus: ...


def decision_step_job_id(external_ref: str, *, requeue: int = 0) -> str:
    """One job per pending decision and queueing of it.

    A second enqueue while that job is queued, retrying or running is dropped
    by streaq rather than asking twice. A queueing by the sweep gets an id of
    its own: streaq keeps a finished job's result under its id for a day, so
    reusing it would read as finished while the new job still waits its turn.
    """
    base = f"workflow-decision:{external_ref}"
    return base if requeue == 0 else f"{base}:{requeue}"


async def enqueue_decision_step(
    external_ref: str, queue: JobQueuePort, *, requeue: int = 0
) -> None:
    try:
        await queue.enqueue(
            DECISION_STEP_JOB,
            external_ref=external_ref,
            _job_id=decision_step_job_id(external_ref, requeue=requeue),
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
        self, uow: SqlAlchemyUnitOfWork, *, queue: DecisionJobQueue | None = None
    ) -> None:
        self._uow = uow
        self._queue = queue

    def ask_once_committed(self, external_ref: str, *, requeue: int = 0) -> None:
        self._uow.after_commit(
            lambda: enqueue_decision_step(
                external_ref, self._job_queue(), requeue=requeue
            )
        )

    async def job_state(
        self, external_ref: str, *, requeue: int = 0
    ) -> DecisionJobState | None:
        try:
            status = await self._job_queue().status(
                decision_step_job_id(external_ref, requeue=requeue)
            )
        except RedisError, OSError, TimeoutError:
            logger.warning(
                "workflow.decision_queue.job_status_unknown.degraded",
                external_ref=external_ref,
                exc_info=True,
            )
            return None
        if status in _ALIVE:
            return "alive"
        # A finished job keeps its result for a day; one that never reached
        # the queue -- its enqueue lost after the commit -- has nothing.
        return "ended" if status is TaskStatus.DONE else "missing"

    def _job_queue(self) -> DecisionJobQueue:
        return self._queue if self._queue is not None else get_streaq_job_queue()
