"""Advancing workflow runs as FAST work: ``workflow.resume``, ``.wait_expire``, ``.start``.

Each is one transaction under the item's row lock, with no network: lock the
run, check the wait is still ACTIVE (a duplicate or late item is a no-op), and
advance inline. Advancing writes the next suspension's wait and its items in
the same transaction (``execution/wait_work.py``), and a run that ends admits
its workflow's parked runs there too. So a step's outcome, the next step's
work and the item's deletion commit together, and a crash anywhere repeats a
step instead of losing one.

* ``workflow.resume`` -- a function run the step waits on ended (subscribed to
  ``FunctionRunFinished``, so the function module never names this one), or a
  TIME wait came due. Replaces the ``workflow-function-events`` consumer group,
  ``resume_workflow_run_for_function`` and the poller's timer claimer.
* ``workflow.wait_expire`` -- a wait outlived its ceiling (``wait_ceiling.py``):
  the run fails and the work it waited on is cancelled. Replaces the expiry
  branches of the reconciliation sweep.
* ``workflow.form_expired`` -- a form's question in a member's inbox expired
  (subscribed to channels' ``ChannelQuestionClosed``): the wait it asked for
  ends as a form nobody answered, now rather than at the wait's own expiry.
* ``workflow.admit`` -- admit a workflow's parked runs into room that did not
  come from a run ending (a raised limit, the cutover).
* ``workflow.start`` -- start a run another module asked for (a schedule's
  fire), idempotent on the reserved run id. See ``contracts/run_work.py``.

The executors that run inside these receive a ``WorkWriter``, never a network
client -- ``fast_guard`` refuses this module if it imports one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.domain.errors import DomainError
from app.core.infrastructure.work import (
    DeadContext,
    FastContext,
    PermanentFailure,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    WorkWriter,
    facts,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.channels.contracts import QuestionAsker, QuestionState
from app.modules.channels.contracts.facts import ChannelQuestionClosed
from app.modules.function.contracts.run_facts import FunctionRunFinished
from app.modules.schedule.contracts.dispatch import (
    mark_run_dispatched,
    mark_run_failed,
)
from app.modules.schedule.contracts.reserved_runs import load_reserved_run
from app.modules.workflow.api.dependencies import build_workflow_engine
from app.modules.workflow.contracts.run_work import (
    WORKFLOW_START,
    WorkflowStartRefused,
    WorkflowStartRequest,
)
from app.modules.workflow.domain.context import TriggerContext
from app.modules.workflow.domain.run import WorkflowRunEntity
from app.modules.workflow.domain.wait import (
    wait_expire_key,
    WorkflowRunWaitEntity,
    WorkflowRunWaitStatus,
    WorkflowRunWaitType,
)
from app.modules.workflow.domain.start import WorkflowStartType
from app.modules.workflow.execution.engine import WorkflowEngine
from app.modules.workflow.execution.resume_target import ResumeTarget
from app.modules.workflow.execution.wait_ceiling import ceiling_seconds, expiry_error
from app.modules.workflow.services.run_resume_service import RunResumeService
from app.modules.workflow.services.schedule_start_service import ScheduleStartService
from app.modules.workflow.execution.run_admission import admit_queued_runs
from app.modules.workflow.infrastructure.form_questions import form_wait_id
from app.modules.workflow.execution.wait_work import (
    WORKFLOW_ADMIT,
    WORKFLOW_FORM_EXPIRED,
    WORKFLOW_RESUME,
    WORKFLOW_WAIT_EXPIRE,
    AdmitWork,
    ResumeWork,
    WaitWork,
    cancel_wait_expire,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

logger = get_logger(__name__)

_FUNCTION = WorkflowRunWaitType.FUNCTION
_TIME = WorkflowRunWaitType.TIME


def _engine(uow: SqlAlchemyUnitOfWork, work: WorkWriter) -> WorkflowEngine:
    return build_workflow_engine(uow, work=work)


async def _resume(
    engine: WorkflowEngine,
    wait: WorkflowRunWaitEntity,
    output: dict[str, object],
) -> None:
    """Resume ``wait``'s run as its owner, as of now -- a resumed step acts for them."""
    assert wait.external_ref is not None
    # Locked here rather than inside the engine: the owner is read from the
    # same row the resume must hold anyway.
    run: WorkflowRunEntity | None = await engine.run_repo.get_for_update(wait.run_id)
    if run is None:
        return
    ctx = await create_authorization_data_service(engine.uow).build_user_context(
        user_id=run.user_id, pod_id=run.pod_id
    )
    token = set_current_context(ctx)
    try:
        await engine.resume_internal(
            wait.wait_type,
            wait.external_ref,
            output,
            ctx=ctx,
            found=ResumeTarget(wait=wait, locked_run=run),
        )
    finally:
        reset_current_context(token)


async def _resume_for_function(engine: WorkflowEngine, function_run_id: UUID) -> None:
    ref = str(function_run_id)
    wait = await engine.wait_repo.find_active_by_external_ref(_FUNCTION, ref)
    if wait is None:
        # Most function runs are not a workflow step's, and a step that
        # already moved on (cancelled, expired) has nothing left to wake.
        return
    status = await engine.function_adapter.get_run_status(function_run_id)
    outcome = status.get("status")
    if outcome == "COMPLETED":
        output = status.get("output_data")
        await _resume(engine, wait, output if isinstance(output, dict) else {})
        return
    if outcome in {"FAILED", "NOT_FOUND"}:
        error = str(status.get("error") or "Function run no longer exists")
        await engine.fail_internal(_FUNCTION, ref, error=error, output={"error": error})


async def resume_run(ctx: FastContext, payload: ResumeWork) -> None:
    engine = _engine(ctx.uow, ctx.work)
    if payload.function_run_id is not None:
        await _resume_for_function(engine, payload.function_run_id)
        return
    if payload.wait_type is _TIME and payload.external_ref:
        wait = await engine.wait_repo.find_active_by_external_ref(
            _TIME, payload.external_ref
        )
        if wait is None:
            return
        fired_at = datetime.now(UTC).isoformat()
        await _resume(
            engine,
            wait,
            {"payload": {"fired_at": fired_at}, "metadata": {}, "llm_output": {}},
        )
        return
    raise PermanentFailure(f"workflow.resume names nothing to wake: {payload}")


async def on_resume_dead(ctx: DeadContext) -> None:
    """A wake that kept failing fails its run rather than leaving it parked."""
    try:
        payload = ResumeWork.model_validate(ctx.item.payload)
    except ValueError:
        return
    engine = _engine(ctx.uow, ctx.work)
    reason = f"The step could not be resumed ({ctx.reason}): {ctx.last_error or ''}"
    if payload.function_run_id is not None:
        await engine.fail_internal(
            _FUNCTION, str(payload.function_run_id), error=reason
        )
    elif payload.wait_type is not None and payload.external_ref:
        await engine.fail_internal(
            payload.wait_type, payload.external_ref, error=reason
        )


async def expire_wait(ctx: FastContext, payload: WaitWork) -> None:
    engine = _engine(ctx.uow, ctx.work)
    wait = await engine.wait_repo.get(payload.wait_id)
    if wait is None or wait.status != WorkflowRunWaitStatus.ACTIVE:
        return
    now = datetime.now(UTC)
    if wait.wait_type is WorkflowRunWaitType.AGENT:
        again = await RunResumeService(engine).expire_agent_wait(wait, now=now)
        if again is not None:
            await ctx.work.add(
                WORKFLOW_WAIT_EXPIRE,
                ctx.item.ns,
                payload,
                run_at=again,
                dedup_key=f"workflow-expire:{wait.id}",
            )
        return
    seconds = ceiling_seconds(wait)
    if seconds is None:
        return
    created = wait.created_at or now
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    due = created + timedelta(seconds=seconds)
    if now < due:
        # Written early (a test pulled it forward, or the ceiling grew); the
        # same item at the real time.
        await ctx.work.add(
            WORKFLOW_WAIT_EXPIRE,
            ctx.item.ns,
            payload,
            run_at=due,
            dedup_key=wait_expire_key(wait.id),
        )
        return
    logger.warning(
        "workflow.reconcile.wait_expired",
        run_id=str(wait.run_id),
        wait_id=str(wait.id),
        wait_type=wait.wait_type.value,
    )
    if await engine.fail_for_wait(wait, error=expiry_error(wait, seconds)) is not None:
        # The work the run waited on stops too: a hung function keeps a
        # sandbox busy for an answer nobody will read.
        await engine.stop_underlying_work(wait)


class FormExpired(BaseModel):
    """The fields of ``ChannelQuestionClosed`` this needs; the rest are ignored."""

    model_config = ConfigDict(extra="ignore")

    ref: str


def expires_a_form(fact: ChannelQuestionClosed) -> bool:
    return (
        fact.state is QuestionState.EXPIRED
        and fact.asker is QuestionAsker.WORKFLOW
        and form_wait_id(fact.ref) is not None
    )


async def expire_form(ctx: FastContext, payload: FormExpired) -> None:
    wait_id = form_wait_id(payload.ref)
    if wait_id is None:
        return
    engine = _engine(ctx.uow, ctx.work)
    wait = await engine.wait_repo.get(wait_id)
    if (
        wait is None
        or wait.status != WorkflowRunWaitStatus.ACTIVE
        or wait.wait_type is not WorkflowRunWaitType.HUMAN
    ):
        return
    seconds = ceiling_seconds(wait) or 0.0
    if await engine.fail_for_wait(wait, error=expiry_error(wait, seconds)) is not None:
        await cancel_wait_expire(ctx.work, pod_id=wait.pod_id, wait_id=wait.id)


async def start_workflow_run(ctx: FastContext, payload: WorkflowStartRequest) -> None:
    engine = _engine(ctx.uow, ctx.work)
    try:
        if payload.schedule_run_id is not None:
            await _start_for_schedule_run(engine, payload.schedule_run_id)
        else:
            await _start_requested(engine, payload)
    except DomainError as exc:
        raise PermanentFailure(exc.message) from exc
    except ValueError as exc:
        # The workflow was deleted between the request and now.
        raise PermanentFailure(str(exc)) from exc


async def _start_for_schedule_run(
    engine: WorkflowEngine, schedule_run_id: UUID
) -> None:
    reserved = await load_reserved_run(engine.uow, schedule_run_id)
    if reserved is None:
        return
    await ScheduleStartService(engine).start_for_fire(
        schedule_id=str(reserved.schedule_id),
        user_id=reserved.user_id,
        payload=reserved.payload,
        schedule_event_id=reserved.source_event_id,
        metadata=reserved.metadata,
        llm_output=reserved.llm_output,
        source_occurred_at=reserved.source_occurred_at,
    )


async def _start_requested(
    engine: WorkflowEngine, payload: WorkflowStartRequest
) -> None:
    assert payload.flow_id and payload.user_id and payload.run_id
    flow = await engine.flow_repo.get(payload.flow_id)
    if flow is None:
        raise ValueError(f"Workflow {payload.flow_id} not found")
    trigger = (
        TriggerContext(
            trigger_type=WorkflowStartType(payload.trigger.trigger_type),
            payload=payload.trigger.payload,
            metadata=payload.trigger.metadata,
            llm_output=payload.trigger.llm_output,
        )
        if payload.trigger is not None
        else None
    )
    user_ctx = await create_authorization_data_service(engine.uow).build_user_context(
        user_id=payload.user_id, pod_id=flow.pod_id
    )
    token = set_current_context(user_ctx)
    try:
        await engine.start_run(
            payload.flow_id,
            payload.user_id,
            run_id=payload.run_id,
            trigger=trigger,
            schedule_event_id=payload.schedule_event_id,
            ctx=user_ctx,
            flow=flow,
        )
    finally:
        reset_current_context(token)
    if payload.dispatches_schedule_run_id is not None:
        await mark_run_dispatched(engine.uow, payload.dispatches_schedule_run_id)


async def on_start_dead(ctx: DeadContext) -> None:
    """Whoever asked for the run hears that it was never started."""
    try:
        payload = WorkflowStartRequest.model_validate(ctx.item.payload)
    except ValueError:
        return
    error = ctx.last_error or ctx.reason
    if payload.schedule_run_id is not None:
        await mark_run_failed(
            ctx.uow, payload.schedule_run_id, RuntimeError(f"{ctx.reason}: {error}")
        )
    if not ctx.item.ns.startswith("pod:"):
        return
    await facts.emit(
        ctx.uow,
        WorkflowStartRefused(
            pod_id=UUID(ctx.item.ns.removeprefix("pod:")),
            error=error,
            schedule_run_id=payload.schedule_run_id,
            run_id=payload.run_id,
            flow_id=payload.flow_id,
            schedule_event_id=payload.schedule_event_id,
        ),
        writer=ctx.work,
    )


async def admit_parked_runs(ctx: FastContext, payload: AdmitWork) -> None:
    await admit_queued_runs(_engine(ctx.uow, ctx.work), payload.flow_id)


def register_run_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=WORKFLOW_ADMIT,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=AdmitWork,
        retry=RetryPolicy(max_attempts=5),
    )(admit_parked_runs)
    registry.handler(
        kind=WORKFLOW_RESUME,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=ResumeWork,
        retry=RetryPolicy(max_attempts=5),
        on_dead=on_resume_dead,
    )(resume_run)
    registry.handler(
        kind=WORKFLOW_WAIT_EXPIRE,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=WaitWork,
        retry=RetryPolicy(max_attempts=5),
    )(expire_wait)
    registry.handler(
        kind=WORKFLOW_FORM_EXPIRED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=FormExpired,
        retry=RetryPolicy(max_attempts=5),
    )(expire_form)
    registry.handler(
        kind=WORKFLOW_START,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=WorkflowStartRequest,
        retry=RetryPolicy(max_attempts=5),
        on_dead=on_start_dead,
    )(start_workflow_run)


register_run_work(default_registry)
facts.subscribe(
    ChannelQuestionClosed,
    kind=WORKFLOW_FORM_EXPIRED,
    cls=WorkClass.EVENTS,
    when=expires_a_form,
    dedup=lambda fact: f"workflow-form-expired:{fact.message_id}",
)
facts.subscribe(
    FunctionRunFinished,
    kind=WORKFLOW_RESUME,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"workflow-resume:fn:{fact.function_run_id}",
)
