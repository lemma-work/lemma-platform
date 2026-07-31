"""Workflow engine: transactions, authorization, and wait lifecycle.

The engine owns one transaction per advance: the run row and its wait row
always commit atomically. Resume paths row-lock the run, so double-resumes
and stale completion events are conflicts/no-ops by construction.
"""

from typing import Any, Dict, List, Optional
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, best_match
from sqlalchemy.exc import IntegrityError

from app.core.authorization.context import Context, ResourceRef, ResourceType
from app.core.authorization.permissions import Permissions
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.workflow.domain.context import TriggerContext, normalize_node_output
from app.modules.workflow.domain.errors import (
    WorkflowAccessDeniedError,
    WorkflowConflictError,
    WorkflowDomainError,
    WorkflowValidationError,
)
from app.modules.workflow.domain.schema_template import defaults_from_schema
from app.modules.workflow.domain.workflow import WorkflowEntity
from app.modules.workflow.domain.run import WorkflowRunEntity, WorkflowRunStatus
from app.modules.workflow.domain.wait import (
    WaitRequest,
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.execution.stepper import RunStepper, StepResult
from app.modules.workflow.infrastructure.adapters import (
    AgentControlAdapter,
    FunctionControlAdapter,
    ScheduleControlAdapter,
)
from app.modules.workflow.infrastructure.repositories import (
    SqlAlchemyWorkflowRepository,
    SqlAlchemyWorkflowRunRepository,
    SqlAlchemyWorkflowRunWaitRepository,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


class FormNodeMismatchError(WorkflowDomainError):
    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="WORKFLOW_FORM_NODE_MISMATCH",
            status_code=422,
        )


class FormValidationError(WorkflowDomainError):
    """Submitted form inputs failed validation against the resolved schema."""

    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="WORKFLOW_FORM_VALIDATION_FAILED",
            status_code=422,
        )


class WorkflowEngine:
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        agent_adapter=None,
        function_adapter=None,
        schedule_adapter=None,
    ):
        self.uow = uow
        self.flow_repo = SqlAlchemyWorkflowRepository(uow)
        self.run_repo = SqlAlchemyWorkflowRunRepository(uow)
        self.wait_repo = SqlAlchemyWorkflowRunWaitRepository(uow)

        self.agent_adapter = agent_adapter or AgentControlAdapter(uow)
        self.function_adapter = function_adapter or FunctionControlAdapter(uow)
        self.schedule_adapter = schedule_adapter or ScheduleControlAdapter(uow)

    def _stepper(self, ctx: Context | None) -> RunStepper:
        return RunStepper(
            agent=self.agent_adapter,
            function=self.function_adapter,
            schedule=self.schedule_adapter,
            authz_ctx=ctx,
        )

    async def _require_action(
        self,
        *,
        requester_user_id: UUID | None,
        action: str,
        pod_id: UUID,
        flow_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> Context | None:
        if requester_user_id is None:
            return ctx

        auth_ctx = ctx or await AuthorizationDataService(
            self.uow.session
        ).build_user_context(
            user_id=requester_user_id,
            pod_id=pod_id,
        )
        await auth_ctx.require(
            action,
            ResourceRef(
                resource_type=ResourceType.WORKFLOW if flow_id else ResourceType.POD,
                resource_id=flow_id or pod_id,
                pod_id=pod_id,
            ),
        )
        return auth_ctx

    # -- run lifecycle ----------------------------------------------------------

    async def start_run(
        self,
        flow_id: UUID,
        user_id: UUID,
        *,
        trigger: TriggerContext | None = None,
        schedule_event_id: str | None = None,
        ctx: Context | None = None,
    ) -> WorkflowRunEntity:
        """Create and advance a new run.

        Manual runs take no inputs: if the entry node is a form the run is
        persisted WAITING on it in the same transaction. Machine waits such
        as agent, function job, and timer waits keep the run RUNNING while an
        active wait row tracks resumability. `trigger` is set only for
        scheduled/event/datastore starts and populates `start.*`.
        """
        flow = await self.flow_repo.get(flow_id)
        if not flow:
            raise ValueError(f"Workflow {flow_id} not found")
        ctx = await self._require_action(
            requester_user_id=user_id,
            action=Permissions.WORKFLOW_EXECUTE,
            pod_id=flow.pod_id,
            flow_id=flow.id,
            ctx=ctx,
        )

        entry_node_id = self._entry_node_id(flow)

        if schedule_event_id is not None:
            existing = await self.run_repo.find_by_schedule_event(
                flow_id=flow.id,
                user_id=user_id,
                schedule_event_id=schedule_event_id,
            )
            if existing is not None:
                return existing

        run = WorkflowRunEntity.create(
            flow_id=flow.id,
            pod_id=flow.pod_id,
            user_id=user_id,
            entry_node_id=entry_node_id,
            trigger=trigger,
            schedule_event_id=schedule_event_id,
        )

        # Persist the run row BEFORE advancing. The (flow_id, user_id,
        # schedule_event_id) unique constraint must gate node side effects, not just
        # the row: create() flushes, so a redelivered scheduled fire raises
        # IntegrityError here — we convert it to WorkflowConflictError WITHOUT ever
        # calling advance(), so a duplicate can never double-run entry-node side
        # effects (start a conversation, invoke a function). We deliberately keep the
        # in-memory `run` rather than create()'s return value, which is a summary
        # entity missing the execution context/stack/step history the stepper needs.
        try:
            await self.run_repo.create(run)
        except IntegrityError as exc:
            if schedule_event_id is None:
                raise
            raise WorkflowConflictError(
                "Workflow run already exists for this schedule event"
            ) from exc

        result = await self._stepper(ctx).advance(run, flow)

        # create() persisted only the entry-node state; write the post-advance state
        # (final node, status, step history, context) before committing.
        run = await self.run_repo.update(run)
        await self._persist_wait(run, result)
        await self.uow.commit()

        return run

    async def submit_form(
        self,
        run_id: UUID,
        node_id: str,
        inputs: Dict[str, Any],
        *,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> WorkflowRunEntity:
        """Submit a form to the run's active HUMAN wait and continue."""
        run = await self.run_repo.get_for_update(run_id)
        if not run:
            raise ValueError("Workflow run not found")
        flow = await self.flow_repo.get(run.flow_id)
        if not flow:
            raise ValueError("Workflow definition not found")
        ctx = await self._require_action(
            requester_user_id=requester_user_id or run.user_id,
            action=Permissions.WORKFLOW_EXECUTE,
            pod_id=run.pod_id,
            flow_id=flow.id,
            ctx=ctx,
        )

        wait = await self.wait_repo.get_active_for_run(run.id)
        if (
            run.status != WorkflowRunStatus.WAITING
            or wait is None
            or wait.wait_type != WorkflowRunWaitType.HUMAN
        ):
            raise WorkflowConflictError("Workflow run is not waiting on a form")
        if wait.node_id != node_id:
            raise FormNodeMismatchError(
                f"Form node mismatch: the active wait is on '{wait.node_id}', "
                f"not '{node_id}'"
            )
        await self._check_assignee(wait, run.pod_id, requester_user_id)

        # The resolved schema rides on the wait. Fill omitted fields from
        # schema defaults (user submission wins), then validate the merged
        # values against that schema so dynamic enums are enforced server-side.
        schema = wait.payload.get("input_schema")
        merged = {**defaults_from_schema(schema), **dict(inputs)}
        self._validate_form_inputs(node_id, schema, merged)

        wait.complete(merged)
        await self.wait_repo.update(wait)

        run.resume(node_id, merged)
        result = await self._stepper(ctx).continue_after(run, flow, node_id)

        run = await self.run_repo.update(run)
        await self._persist_wait(run, result)
        await self.uow.commit()
        return run

    async def resume_internal(
        self,
        wait_type: WorkflowRunWaitType,
        external_ref: str,
        output: Dict[str, Any] | None = None,
        *,
        ctx: Context | None = None,
    ) -> WorkflowRunEntity | None:
        """Resume the run waiting on (wait_type, external_ref).

        Returns None when no ACTIVE wait matches — stale or duplicate
        completion events are no-ops by construction.
        """
        wait = await self.wait_repo.find_active_by_external_ref(wait_type, external_ref)
        if wait is None:
            logger.debug("workflow.resume.stale_event", wait_type=wait_type.value)
            return None

        run = await self.run_repo.get_for_update(wait.run_id)
        if run is None or run.status not in (
            WorkflowRunStatus.WAITING,
            WorkflowRunStatus.RUNNING,
        ):
            logger.debug(
                "workflow.resume.stale_event",
                wait_type=wait_type.value,
                run_status=run.status.value if run else "MISSING",
            )
            return None

        flow = await self.flow_repo.get(run.flow_id)
        if not flow:
            raise ValueError("Workflow definition not found")

        # External completions (agents without an output_schema, JOB functions)
        # may hand back a bare string or other non-dict. Normalize to a dict so
        # the wait payload and node output are consistent and the resume never
        # crashes on dict(output).
        normalized = normalize_node_output(output)
        wait.complete(normalized or None)
        await self.wait_repo.update(wait)

        run.resume(wait.node_id, normalized)
        result = await self._stepper(ctx).continue_after(run, flow, wait.node_id)

        run = await self.run_repo.update(run)
        await self._persist_wait(run, result)
        await self.uow.commit()
        return run

    async def fail_internal(
        self,
        wait_type: WorkflowRunWaitType,
        external_ref: str,
        error: str,
        output: Dict[str, Any] | None = None,
    ) -> WorkflowRunEntity | None:
        """Fail the run waiting on (wait_type, external_ref)."""
        wait = await self.wait_repo.find_active_by_external_ref(wait_type, external_ref)
        if wait is None:
            logger.debug("workflow.fail.stale_event", wait_type=wait_type.value)
            return None
        run = await self.run_repo.get_for_update(wait.run_id)
        if run is None or run.status not in (
            WorkflowRunStatus.WAITING,
            WorkflowRunStatus.RUNNING,
        ):
            return None

        normalized = normalize_node_output(output)
        wait.fail(normalized or {"error": error})
        await self.wait_repo.update(wait)
        if normalized:
            run.record_node_output(wait.node_id, {**normalized, "error": error})
        run.fail(error, node_id=wait.node_id)
        run = await self.run_repo.update(run)
        await self.uow.commit()
        return run

    async def cancel_run(
        self,
        run_id: UUID,
        *,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> WorkflowRunEntity:
        """Cancel a non-terminal run and its active wait atomically."""
        run = await self.run_repo.get_for_update(run_id)
        if not run:
            raise ValueError("Workflow run not found")
        await self._require_action(
            requester_user_id=requester_user_id or run.user_id,
            action=Permissions.WORKFLOW_EXECUTE,
            pod_id=run.pod_id,
            flow_id=run.flow_id,
            ctx=ctx,
        )
        wait = await self.wait_repo.get_active_for_run(run.id)
        if wait is not None:
            wait.cancel()
            await self.wait_repo.update(wait)
            if wait.wait_type in (
                WorkflowRunWaitType.AGENT,
                WorkflowRunWaitType.FUNCTION,
            ):
                # The underlying work is not stopped; its completion event
                # will find no ACTIVE wait and be dropped with a log line.
                logger.debug(
                    "workflow.cancel.underlying_work_left_running",
                    run_id=str(run.id),
                    wait_type=wait.wait_type.value,
                )
        try:
            run.cancel()
        except ValueError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        run = await self.run_repo.update(run)
        await self.uow.commit()
        logger.debug("workflow.run.cancelled", run_id=str(run.id))
        return run

    # -- queries -----------------------------------------------------------------

    async def get_run(
        self,
        run_id: UUID,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> Optional[WorkflowRunEntity]:
        run = await self.run_repo.get(run_id)
        if run:
            await self._require_action(
                requester_user_id=requester_user_id,
                action=Permissions.WORKFLOW_READ,
                pod_id=run.pod_id,
                flow_id=run.flow_id,
                ctx=ctx,
            )
        return run

    async def get_active_wait(self, run_id: UUID) -> WorkflowRunWaitEntity | None:
        return await self.wait_repo.get_active_for_run(run_id)

    async def list_runs(
        self,
        flow_id: UUID,
        limit: int = 100,
        cursor: UUID | None = None,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> tuple[List[WorkflowRunEntity], UUID | None]:
        flow = await self.flow_repo.get(flow_id)
        if flow:
            await self._require_action(
                requester_user_id=requester_user_id,
                action=Permissions.WORKFLOW_READ,
                pod_id=flow.pod_id,
                flow_id=flow.id,
                ctx=ctx,
            )
        return await self.run_repo.list_by_flow(
            flow_id,
            limit=limit,
            cursor=cursor,
        )

    # -- internals ---------------------------------------------------------------

    def _entry_node_id(self, flow: WorkflowEntity) -> str:
        if not flow.nodes:
            raise WorkflowValidationError(
                "Workflow has no graph yet — upload nodes before starting runs"
            )
        if flow.entry_node_id and flow.has_node(flow.entry_node_id):
            return flow.entry_node_id
        # Stored before entry ids existed: validate now (raises
        # GraphValidationError for invalid graphs) and use the computed entry.
        flow.validate_graph()
        assert flow.entry_node_id is not None
        return flow.entry_node_id

    async def _persist_wait(self, run: WorkflowRunEntity, result: StepResult) -> None:
        if result.wait is None or run.status not in (
            WorkflowRunStatus.WAITING,
            WorkflowRunStatus.RUNNING,
        ):
            return
        assert run.current_node_id is not None
        await self.wait_repo.create(self._wait_entity(run, result.wait))

    def _wait_entity(
        self, run: WorkflowRunEntity, request: WaitRequest
    ) -> WorkflowRunWaitEntity:
        payload = dict(request.payload)
        if request.scheduled_at is not None:
            payload.setdefault("scheduled_at", request.scheduled_at.isoformat())
        return WorkflowRunWaitEntity(
            run_id=run.id,
            flow_id=run.flow_id,
            pod_id=run.pod_id,
            node_id=run.current_node_id,
            wait_type=request.wait_type,
            assigned_pod_member_id=request.assigned_pod_member_id,
            external_ref=request.external_ref,
            payload=payload,
        )

    @staticmethod
    def _validate_form_inputs(
        node_id: str, schema: Any | None, data: Dict[str, Any]
    ) -> None:
        """Validate submitted form values against the resolved schema stored on
        the wait. A malformed schema (already validated at suspend) is treated
        as no-schema rather than blocking the user."""
        if not isinstance(schema, dict) or not schema:
            return
        try:
            validator = Draft202012Validator(schema)
        except SchemaError:
            logger.warning("workflow.form.invalid_schema", node_id=node_id)
            return
        error = best_match(validator.iter_errors(data))
        if error is not None:
            field = ".".join(str(part) for part in error.absolute_path) or "input"
            raise FormValidationError(
                f"Form input for node '{node_id}' is invalid at '{field}': "
                f"{error.message}"
            )

    async def _check_assignee(
        self,
        wait: WorkflowRunWaitEntity,
        pod_id: UUID,
        requester_user_id: UUID | None,
    ) -> None:
        if wait.assigned_pod_member_id is None or requester_user_id is None:
            return
        from app.composition.workflow_pod import (
            PodMemberRepository,
        )

        pod_member = await PodMemberRepository(self.uow).get_by_pod_and_user_id(
            pod_id,
            requester_user_id,
        )
        if pod_member is None or pod_member.id != wait.assigned_pod_member_id:
            raise WorkflowAccessDeniedError(
                "Workflow wait is assigned to another pod member"
            )
