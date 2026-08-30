"""Picking up an agent run whose worker went away.

A release used to end every conversation in flight: the departing worker
finalized each run FAILED, and streaq recorded the interrupted job as
*succeeded*, so nothing redelivered it. Shipping a version terminated every run
on the box and each person had to ask again.

Nothing needs re-doing to fix that. Messages are persisted as they stream and
history is rebuilt from the database on every run, so a run that resumes
reconstructs exactly the context the interrupted one had and carries on.
"""

from __future__ import annotations

from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import SharedStreaqJobQueue
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.run_projections import ResumableAgentRunRef

logger = get_logger(__name__)


def agent_run_job_id(agent_run_id: UUID, *, attempt: int = 0) -> str:
    """The streaq job id for a run, distinct per resume attempt.

    The id exists to deduplicate: streaq publishes with `SET ... NX`, so a
    second enqueue under an id that already exists is dropped -- and dropped
    silently, because `enqueue` returns a Task exactly as it would for a real
    one. That is what stops a run being started twice.

    It is also what made resuming under the original id a coin flip. The job
    that was interrupted has already finished, but its task key lingers for the
    result TTL, so a sweep that ran inside that window enqueued nothing, the run
    sat parked, and its attempts drained away against a job that never existed.
    Observed working only because the key had expired in the 74 seconds between
    the worker restarting and the sweep firing.

    So each attempt gets its own id. Deduplication still holds where it matters
    -- two sweeps racing on the same attempt collide as before.
    """
    suffix = f":resume-{attempt}" if attempt else ""
    return f"agent-run:{agent_run_id}{suffix}"


#: How many times a run may be handed on before we stop trying. A run that is
#: interrupted immediately on every attempt is not making progress, and the
#: alternative to a bound is a job that outlives the reason anyone wanted it.
_MAX_RESUME_ATTEMPTS = 5


async def resume_parked_agent_runs(
    *,
    uow_factory: SessionUnitOfWorkFactory,
    job_queue: SharedStreaqJobQueue,
) -> None:
    """Hand parked runs to a live worker.

    A release used to end every in-flight run: the worker finalized them FAILED
    on its way out and the person had to ask again. They are parked INTERRUPTED
    now, and this picks them back up -- everything the run did is already
    persisted, so the run that resumes rebuilds the same history and carries on
    from where it stopped.

    Every two minutes rather than with the ten-minute reconciler: this is the
    latency a person waits through after a deploy, and the query is one indexed
    read that returns nothing on almost every tick.
    """
    try:
        async with uow_factory() as uow:
            repo = ConversationRepository(uow)
            parked = await repo.list_resumable_runs()
            exhausted: list[UUID] = []
            resumable: list[ResumableAgentRunRef] = []
            for run in parked:
                if run.resume_attempts >= _MAX_RESUME_ATTEMPTS:
                    exhausted.append(run.id)
                    continue
                await repo.record_resume_attempt(run.id)
                resumable.append(run)
            for agent_run_id in exhausted:
                await repo.finish_agent_run(
                    agent_run_id=agent_run_id,
                    status=AgentRunStatus.FAILED,
                    error=(
                        "Agent run could not be resumed after repeated worker restarts"
                    ),
                )
    # The failures a sweep should survive and try again on next tick: a database
    # or storage blip, a denied read. Not a TypeError -- that is a bug in this
    # function, and swallowing it would leave every parked run parked forever
    # with nothing but a warning to show for it.
    except DomainError, SQLAlchemyError, OSError, TimeoutError:
        logger.error(
            "agent.handlers.resume_interrupted_agent_runs_cron.failed", exc_info=True
        )
        return

    for run in resumable:
        try:
            await job_queue.enqueue(
                "process_agent_run",
                context={
                    "agent_run_id": str(run.id),
                    "conversation_id": str(run.conversation_id),
                    "user_id": str(run.user_id),
                    "pod_id": str(run.pod_id),
                    "agent_name": None,
                },
                # The attempt this enqueue *is*: `record_resume_attempt` has
                # already counted it, so the stored value is one ahead of what
                # was read.
                _job_id=agent_run_job_id(run.id, attempt=run.resume_attempts + 1),
            )
        # One run that cannot be enqueued must not strand the others; the next
        # tick tries it again.
        except RedisError, OSError, TimeoutError:
            logger.warning(
                "agent.handlers.resume_enqueue_failed.degraded",
                agent_run_id=str(run.id),
                exc_info=True,
            )
    if resumable or exhausted:
        logger.info(
            "agent.handlers.interrupted_runs_resumed.observed",
            resumed_count=len(resumable),
            exhausted_count=len(exhausted),
        )
