"""What the engine writes and tells others once a run has moved.

Split out of ``engine.py`` at the 600-line ceiling ``scripts/check_architecture.py``
enforces. Every transition the engine makes ends the same way, and this is that
tail, kept as a base of the engine rather than a collaborator for the reason
``wait_failure.py`` gives: it is engine bookkeeping that must be identical on
every path.

* ``_persist_wait`` writes the wait the stepper asked for and the work items
  that end it (``wait_work.py``), in the run's transaction.
* ``_record_transition`` -- for a run that just ended -- records its terminal
  event, announces ``WorkflowRunFinished`` to subscribers, and admits the
  workflow's parked runs into the place it freed, all in the same transaction
  and under the workflow's row lock. Nothing about a finish happens after the
  commit any more, so nothing about it can be lost to a crash after the commit.
* ``_settle`` commits -- unless the engine runs inside a work handler, whose
  runtime owns the commit -- and pushes the committed run to anyone streaming
  it once the commit has happened.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter, facts
from app.core.log.log import get_logger
from app.modules.workflow.contracts.run_work import WorkflowRunFinished
from app.modules.workflow.domain.events import WorkflowRunTerminalEvent
from app.modules.workflow.domain.ports import FormQuestionPort
from app.modules.workflow.domain.run import (
    TERMINAL_STATUSES,
    WorkflowRunEntity,
    WorkflowRunStatus,
)
from app.modules.workflow.domain.wait import WorkflowRunWaitEntity
from app.modules.workflow.execution.stepper import StepResult
from app.modules.workflow.execution.wait_persistence import wait_entity_for
from app.modules.workflow.execution.wait_work import write_wait_items
from app.modules.workflow.infrastructure.repositories import (
    SqlAlchemyWorkflowRepository,
    SqlAlchemyWorkflowRunRepository,
    SqlAlchemyWorkflowRunWaitRepository,
)
from app.modules.workflow.infrastructure.run_channel import (
    publish_run_state,
    run_is_watched,
)

logger = get_logger(__name__)


class RunPublication:
    uow: SqlAlchemyUnitOfWork
    work: WorkWriter
    commits: bool
    flow_repo: SqlAlchemyWorkflowRepository
    run_repo: SqlAlchemyWorkflowRunRepository
    wait_repo: SqlAlchemyWorkflowRunWaitRepository
    form_questions: FormQuestionPort

    async def _record_transition(
        self, run: WorkflowRunEntity, *, admit: bool = True
    ) -> None:
        """A run that ended says so, and hands its place on, in this transaction."""
        if run.status not in TERMINAL_STATUSES:
            return
        self.uow.collect_events([WorkflowRunTerminalEvent.from_run(run)])
        await facts.emit(
            self.uow,
            WorkflowRunFinished(
                run_id=run.id,
                pod_id=run.pod_id,
                flow_id=run.flow_id,
                status=WorkflowRunStatus(run.status).value,
                schedule_event_id=run.schedule_event_id,
                error=run.error,
            ),
            writer=self.work,
        )
        if admit:
            from app.modules.workflow.execution.run_admission import (
                admit_queued_runs,
            )

            await admit_queued_runs(self, run.flow_id)  # type: ignore[arg-type]

    async def _settle(self, run: WorkflowRunEntity) -> None:
        """Commit (when the engine owns the transaction), then tell watchers."""
        self.uow.after_commit(lambda: self._announce(run))
        if self.commits:
            await self.uow.commit()

    async def _announce(self, run: WorkflowRunEntity) -> None:
        """Push the committed run to anyone streaming it.

        Runs only after the commit -- a subscriber must never observe state the
        database has not accepted. Imported lazily to keep the API schema
        module out of the engine's import cycle.
        """
        from app.modules.workflow.api.schemas import run_response_from_domain

        # Everything here is best effort and must stay that way: the run is
        # already committed, so a failure to *tell anyone* cannot be allowed to
        # turn a successful advance into a raised exception. Clients still poll.
        try:
            if not await run_is_watched(run.id):
                return
            wait = await self.wait_repo.get_active_for_run(run.id)
            title = await self.flow_repo.run_title_of(run.flow_id)
            payload = run_response_from_domain(run, wait, title).model_dump(mode="json")
            # The wait read above needs a connection; the Redis publish does
            # not, and this runs after every node advance.
            async with connection_released(getattr(self.wait_repo, "session", None)):
                await publish_run_state(
                    run.id,
                    payload,
                    terminal=run.status in TERMINAL_STATUSES,
                )
        except Exception:
            logger.debug("workflow.run.announce_failed", exc_info=True)

    async def _persist_wait(
        self, run: WorkflowRunEntity, result: StepResult
    ) -> WorkflowRunWaitEntity | None:
        """Write the wait row and the work that ends it, in the run's transaction.

        Telling a form's assignee is one of those items rather than a send made
        here: a Slack, email or Teams call inside the run transaction held the
        run's row lock and a pooled connection for as long as the platform took
        to answer, and one made after the commit was lost to a crash in between.
        """
        if result.wait is None or run.status not in (
            WorkflowRunStatus.WAITING,
            WorkflowRunStatus.RUNNING,
        ):
            return None
        wait = await self.wait_repo.create(wait_entity_for(run, result.wait))
        await write_wait_items(self.work, wait, now=datetime.now(UTC))
        return wait
