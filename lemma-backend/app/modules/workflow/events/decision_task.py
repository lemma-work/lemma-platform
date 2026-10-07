"""The job that asks a workflow step's decision: `decide_workflow_step`.

Queued after the commit that suspends a run on a DECISION wait
(`infrastructure/decision_queue.py`), and again by the reconciliation sweep
when that job was lost. One job per pending decision; a duplicate finds no
active wait and does nothing.
"""

from __future__ import annotations

from typing import cast

from streaq import StreaqRetry
from streaq.task import RegisteredTask

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_runtime import (
    AppWorkerContext,
    streaq_task,
    streaq_worker,
)
from app.core.origin import Origin, OriginKind, origin_scope
from app.modules.decisions.contracts import (
    DecisionCaller,
    DecisionMaker,
    DecisionRequest,
    DecisionResult,
)
from app.modules.decisions.contracts.decide import decision_maker
from app.modules.workflow.api.dependencies import build_workflow_engine
from app.modules.workflow.infrastructure.decision_queue import DECISION_STEP_JOB
from app.modules.workflow.services.decision_resume_service import (
    DecisionResumeService,
    PendingDecision,
)
from app.modules.workflow.services.decision_step import (
    DECISION_STEP_MAX_ATTEMPTS,
    ask_waiting_decision,
)


class UnitOfWorkDecisionWaits:
    """`DecisionWaits` over the database, one short transaction per step."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def pending_decision(self, external_ref: str) -> PendingDecision | None:
        async with self._uow_factory() as uow:
            service = DecisionResumeService(build_workflow_engine(uow))
            return await service.find_pending(external_ref)

    async def route_decision(self, external_ref: str, result: DecisionResult) -> None:
        async with self._uow_factory() as uow:
            service = DecisionResumeService(build_workflow_engine(uow))
            await service.resume_on_answer(external_ref, result)

    async def fail_decision(self, external_ref: str, error: str) -> None:
        async with self._uow_factory() as uow:
            service = DecisionResumeService(build_workflow_engine(uow))
            await service.fail_pending(external_ref, error)


class AskedByWorkflowStep:
    """Asks under the WORKFLOW origin.

    A decision a node asks is work the node sent out, like the agent or
    function run another node starts, and is attributed the same way. Only the
    asking: the run's own resume keeps the origin the run arrived on.
    """

    def __init__(self, maker: DecisionMaker) -> None:
        self._maker = maker

    async def decide(
        self, request: DecisionRequest, caller: DecisionCaller
    ) -> DecisionResult:
        with origin_scope(Origin(OriginKind.WORKFLOW)):
            return await self._maker.decide(request, caller)


@streaq_task(name=DECISION_STEP_JOB, max_tries=DECISION_STEP_MAX_ATTEMPTS)
async def decide_workflow_step(external_ref: str) -> None:
    """Ask the decision a run waits on and resume the run on its route."""
    worker_ctx: AppWorkerContext = streaq_worker.context
    retry = await ask_waiting_decision(
        external_ref,
        attempt=_attempt(),
        waits=UnitOfWorkDecisionWaits(worker_ctx.uow_factory),
        maker=AskedByWorkflowStep(decision_maker()),
    )
    if retry is not None:
        raise StreaqRetry(delay=retry.delay_seconds)


def _attempt() -> int:
    """This run of the job's try number, the first being 1.

    `streaq_task` is untyped, so the name it binds reads as the plain function;
    it is streaq's registered task, which carries the running try's context.
    """
    return cast(RegisteredTask, decide_workflow_step).context.tries
