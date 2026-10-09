"""The transaction-side half of a decision step: what is asked, where it leads.

Each method runs in one short transaction of its own. The question itself is
asked between them, with no session open -- see `decision_step.py` -- because a
model can take as long as its timeout to answer and nothing in the database
needs to wait for it.

Routing reads the flow as it is when the answer arrives, not as it was when the
run reached the node: a route an author fixes while a run waits is the route
that run takes. What was asked stays as it was asked, on the wait row.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.log.log import get_logger
from app.modules.decisions.contracts import (
    DecisionCaller,
    DecisionExample,
    DecisionRequest,
    DecisionResult,
)
from app.modules.workflow.domain.decision_questions import (
    DecisionAsk,
    DecisionRoute,
    choose_route,
    route_key,
)
from app.modules.workflow.domain.nodes import DecisionNode
from app.modules.workflow.domain.nodes.decision import ANSWER_KEY, QuestionAnswer
from app.modules.workflow.domain.run import WorkflowRunStatus
from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.domain.workflow import WorkflowEntity
from app.modules.workflow.execution.engine import WorkflowEngine

logger = get_logger(__name__)

#: How many times the reconciliation sweep queues a lost decision again before
#: it gives up and fails the run. Each queueing gets the job's own retries.
MAX_DECISION_REQUEUES = 3
_REQUEUES = "requeues"
_LIVE = (WorkflowRunStatus.RUNNING, WorkflowRunStatus.WAITING)
_DECISION = WorkflowRunWaitType.DECISION

UserContextBuilder = Callable[[UUID, UUID], Awaitable[Context]]


@dataclass(frozen=True, slots=True)
class PendingDecision:
    """A decision a live run is waiting on, ready to ask."""

    request: DecisionRequest
    caller: DecisionCaller


class DecisionResumeService:
    def __init__(
        self,
        engine: WorkflowEngine,
        *,
        user_context: UserContextBuilder | None = None,
    ) -> None:
        self._engine = engine
        self._user_context = user_context or self._authorization_context

    async def find_pending(self, external_ref: str) -> PendingDecision | None:
        """The decision to ask, or None: the run was cancelled, failed, or the
        decision was already answered -- a duplicate job has nothing to do."""
        wait = await self._engine.wait_repo.find_active_by_external_ref(
            _DECISION, external_ref
        )
        if wait is None:
            return None
        run = await self._engine.run_repo.get(wait.run_id)
        if run is None or run.status not in _LIVE:
            return None
        ctx = await self._user_context(run.user_id, run.pod_id)
        return PendingDecision(
            request=_request(DecisionAsk.from_payload(wait.payload)),
            caller=DecisionCaller(
                user_id=run.user_id,
                organization_id=ctx.organization_id,
                pod_id=run.pod_id,
                workload_type="workflow",
                workload_id=run.flow_id,
                source_type="workflow_decision",
                source_id=str(run.id),
            ),
        )

    async def resume_on_answer(self, external_ref: str, result: DecisionResult) -> bool:
        """Continue the run down the branch the answer chose, or fail it when
        the answer has nowhere to go. Never guesses a branch."""
        wait = await self._engine.wait_repo.find_active_by_external_ref(
            _DECISION, external_ref
        )
        if wait is None:
            return False
        answer = result.answers.get(ANSWER_KEY)
        # A multi-choice is refused when the flow is saved, so an answer is one
        # value or none; a tuple here would be a question this node never asks.
        value = (
            None if answer is None or isinstance(answer.value, tuple) else answer.value
        )
        confidence = None if answer is None else answer.confidence
        output: dict[str, JsonValue] = {
            "answer": value,
            "confidence": confidence,
            "provider": result.provider,
            "model": result.model,
        }
        flow = await self._engine.flow_repo.get(wait.flow_id)
        route = _route(flow, wait.node_id, value, confidence)
        if isinstance(route, str):
            await self._engine.fail_internal(
                _DECISION, external_ref, error=route, output=output
            )
            return True
        output["route"] = route.next_node_id
        run = await self._engine.run_repo.get(wait.run_id)
        if run is None:
            return False
        ctx = await self._user_context(run.user_id, run.pod_id)
        token = set_current_context(ctx)
        try:
            await self._engine.resume_internal(
                _DECISION,
                external_ref,
                output,
                ctx=ctx,
                next_node_id=route.next_node_id,
            )
        finally:
            reset_current_context(token)
        return True

    async def fail_pending(self, external_ref: str, error: str) -> bool:
        failed = await self._engine.fail_internal(_DECISION, external_ref, error=error)
        return failed is not None

    async def recover_lost(self, wait: WorkflowRunWaitEntity) -> bool:
        """Queue a decision again whose job was lost, a bounded number of times.

        A wait this old has a job that is lost or only held up, and the queue
        is asked which:

        - still queued, waiting out a retry, or running: left to answer, and
          not counted;
        - never queued -- its enqueue lost after the commit, as when Redis
          refused it -- so nothing ran and nothing is counted: the same
          queueing is tried again;
        - ended without resolving the wait: that is a loss, so it is queued
          again under an id of its own and counted towards giving up.

        When the queue cannot say, the next sweep asks again rather than count
        a loss that may not be one. A queue that never takes the job is ended
        by the wait's own age ceiling, not here.

        Takes the run's row lock before acting and re-reads the wait under it:
        the job may be answering this very decision, and resuming takes the
        same lock, so whichever comes second sees what the first did.
        """
        external_ref = wait.external_ref
        if not external_ref:
            return False
        state = await self._engine.decision_adapter.job_state(
            external_ref, requeue=_requeues_of(wait)
        )
        if state is None or state == "alive":
            return False
        run = await self._engine.run_repo.get_for_update(wait.run_id)
        current = await self._engine.wait_repo.find_active_by_external_ref(
            _DECISION, external_ref
        )
        if run is None or run.status not in _LIVE or current is None:
            # Nothing to do; end the transaction so the sweep does not carry
            # this run's row lock on through the rest of its batch.
            await self._engine.uow.commit()
            return False
        requeues = _requeues_of(current)
        if state == "missing":
            self._engine.decision_adapter.ask_once_committed(
                external_ref, requeue=requeues
            )
            await self._engine.uow.commit()
            logger.warning(
                "workflow.decision_resume.unqueued_decision_queued.degraded",
                run_id=str(wait.run_id),
                wait_id=str(wait.id),
                requeues=requeues,
            )
            return True
        if requeues >= MAX_DECISION_REQUEUES:
            await self._engine.fail_for_wait(
                current,
                error=(
                    "The job asking this step's question was lost "
                    f"{requeues + 1} times before it could answer, so the run "
                    "was stopped."
                ),
            )
            return True
        current.payload = {**current.payload, _REQUEUES: requeues + 1}
        await self._engine.wait_repo.update(current)
        self._engine.decision_adapter.ask_once_committed(
            external_ref, requeue=requeues + 1
        )
        await self._engine.uow.commit()
        logger.warning(
            "workflow.decision_resume.lost_decision_requeued.degraded",
            run_id=str(wait.run_id),
            wait_id=str(wait.id),
            requeues=requeues + 1,
        )
        return True

    async def _authorization_context(self, user_id: UUID, pod_id: UUID) -> Context:
        return await create_authorization_data_service(
            self._engine.uow
        ).build_user_context(user_id=user_id, pod_id=pod_id)


def _request(ask: DecisionAsk) -> DecisionRequest:
    return DecisionRequest(
        instruction=ask.instruction,
        evidence=ask.evidence,
        schema=ask.schema_,
        examples=tuple(
            DecisionExample(evidence=example.evidence, answers=example.answers)
            for example in ask.examples
        ),
        priority="background",
    )


def _route(
    flow: WorkflowEntity | None,
    node_id: str,
    value: QuestionAnswer | None,
    confidence: float | None,
) -> DecisionRoute | str:
    """The branch this answer takes, or -- as the run's failure -- why none."""
    if flow is None or not flow.has_node(node_id):
        return f"Step '{node_id}' was removed from the workflow while it waited."
    node = flow.get_node(node_id)
    if not isinstance(node, DecisionNode) or node.config.question is None:
        return (
            f"Step '{node_id}' no longer asks a question, so its answer has no route."
        )
    route = choose_route(
        node.config.question,
        value=value,
        confidence=confidence,
        default_next_node_id=flow.next_after(node_id),
    )
    if route.next_node_id is not None:
        return route
    if route.unsure:
        return (
            f"Step '{node_id}' could not tell from the evidence, and has no route "
            "for an unsure answer and no default edge."
        )
    answered = route_key(value) if value is not None else "nothing"
    return (
        f"Step '{node_id}' answered '{answered}', which has no route, and the "
        "step has no default edge."
    )


def _requeues_of(wait: WorkflowRunWaitEntity) -> int:
    raw = wait.payload.get(_REQUEUES)
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
