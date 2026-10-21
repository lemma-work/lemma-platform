"""Workflow steps that leave the database: ``workflow.decide`` and ``workflow.notify_form``.

Both are written with the wait they serve (``execution/wait_work.py``), so a
committed wait always has its job -- the post-commit enqueue they replace could
be lost, which is what the decision ``recover_lost`` requeue and the
reconciliation sweep existed for.

``workflow.decide`` asks the question a DECISION wait holds and routes the run
on the answer, in the item's fenced completion: the answer, the run's next
step and the item's deletion commit together, and a worker that lost its lease
cannot route a second time. A retryable miss (provider down, timed out,
limited) is charged an attempt and retried with backoff; the last attempt
fails the run naming the cause, and never takes a branch.

``workflow.notify_form`` tells a form's assignee it is waiting on them. The
notification is keyed on (run, node), so a repeated item sends nothing twice.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.core.infrastructure.work import (
    DeadContext,
    Exponential,
    ItemFailure,
    JobContext,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
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
from app.modules.workflow.domain.wait import WorkflowRunWaitStatus, WorkflowRunWaitType
from app.modules.workflow.execution.wait_ceiling import expires_at
from app.modules.workflow.execution.wait_work import (
    WORKFLOW_DECIDE,
    WORKFLOW_NOTIFY_FORM,
    DecisionWork,
    WaitWork,
)
from app.modules.workflow.services.decision_resume_service import (
    DecisionResumeService,
    PendingDecision,
)
from app.modules.workflow.services.decision_step import (
    DECISION_STEP_MAX_ATTEMPTS,
    ask_waiting_decision,
)


class WorkDecisionWaits:
    """`DecisionWaits` on a work item: reads in a checkpoint, the answer fenced."""

    def __init__(self, ctx: JobContext) -> None:
        self._ctx = ctx

    async def pending_decision(self, external_ref: str) -> PendingDecision | None:
        async with self._ctx.checkpoint() as uow:
            return await DecisionResumeService(
                build_workflow_engine(uow, work=self._ctx.writer(uow))
            ).find_pending(external_ref)

    async def route_decision(self, external_ref: str, result: DecisionResult) -> None:
        async with self._ctx.complete_work() as uow:
            await DecisionResumeService(
                build_workflow_engine(uow, work=self._ctx.writer(uow))
            ).resume_on_answer(external_ref, result)

    async def fail_decision(self, external_ref: str, error: str) -> None:
        async with self._ctx.complete_work() as uow:
            await DecisionResumeService(
                build_workflow_engine(uow, work=self._ctx.writer(uow))
            ).fail_pending(external_ref, error)


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


def _decide_handler(maker: Callable[[], DecisionMaker]):
    async def decide_step(ctx: JobContext, payload: DecisionWork) -> None:
        retry = await ask_waiting_decision(
            payload.external_ref,
            attempt=ctx.item.attempts + 1,
            waits=WorkDecisionWaits(ctx),
            maker=AskedByWorkflowStep(maker()),
        )
        if retry is not None:
            # Charged: the runtime's backoff spaces the attempts, and the last
            # one fails the run itself rather than going dead.
            raise ItemFailure(f"the decision was not answered; asking again ({retry})")

    return decide_step


async def on_decide_dead(ctx: DeadContext) -> None:
    """The job could not keep running (its workers kept dying): fail the run."""
    try:
        payload = DecisionWork.model_validate(ctx.item.payload)
    except ValueError:
        return
    await DecisionResumeService(
        build_workflow_engine(ctx.uow, work=ctx.work)
    ).fail_pending(
        payload.external_ref,
        f"The question was not asked ({ctx.reason}): {ctx.last_error or ''}",
    )


async def notify_form(ctx: JobContext, payload: WaitWork) -> None:
    async with ctx.checkpoint() as uow:
        engine = build_workflow_engine(uow, work=ctx.writer(uow))
        wait = await engine.wait_repo.get(payload.wait_id)
        if (
            wait is None
            or wait.status != WorkflowRunWaitStatus.ACTIVE
            or wait.wait_type != WorkflowRunWaitType.HUMAN
            or wait.assigned_pod_member_id is None
        ):
            return
        run = await engine.run_repo.get(wait.run_id)
        flow = await engine.flow_repo.get(wait.flow_id)
        if run is None:
            return
        # A question in the assignee's inbox, in this transaction; getting it
        # to them where they are is a delivery channels plans with the line.
        await engine.form_questions.ask_form(
            pod_id=run.pod_id,
            run_id=run.id,
            flow_id=run.flow_id,
            node_id=wait.node_id,
            wait_id=wait.id,
            assigned_pod_member_id=wait.assigned_pod_member_id,
            flow_name=getattr(flow, "name", None),
            schema=wait.payload.get("input_schema"),
            actor_user_id=run.user_id,
            expires_at=expires_at(wait, now=datetime.now(UTC)),
            node_label=_node_label(flow, wait.node_id),
        )


def _node_label(flow: object, node_id: str) -> str | None:
    """The label the workflow's author gave the form's step, if any."""
    for node in getattr(flow, "nodes", None) or ():
        if getattr(node, "id", None) == node_id:
            label = getattr(node, "label", None)
            return label if isinstance(label, str) and label.strip() else None
    return None


def register_step_jobs(
    registry: WorkRegistry, *, maker: Callable[[], DecisionMaker] | None = None
) -> None:
    """Register both kinds; a test passes its own registry and decision maker."""
    registry.handler(
        kind=WORKFLOW_DECIDE,
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=DecisionWork,
        retry=RetryPolicy(
            max_attempts=DECISION_STEP_MAX_ATTEMPTS,
            max_lease_losses=3,
            backoff=Exponential(
                base=timedelta(seconds=5), cap=timedelta(minutes=2), jitter=0.2
            ),
        ),
        on_dead=on_decide_dead,
    )(_decide_handler(maker or decision_maker))
    registry.handler(
        kind=WORKFLOW_NOTIFY_FORM,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=WaitWork,
    )(notify_form)


register_step_jobs(default_registry)
