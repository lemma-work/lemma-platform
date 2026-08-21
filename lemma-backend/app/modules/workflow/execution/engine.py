"""Workflow engine: transactions, authorization, and wait lifecycle.

The engine owns one transaction per advance: the run row and its wait row
always commit atomically. Resume paths row-lock the run, so double-resumes
and stale completion events are conflicts/no-ops by construction.
"""

from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.authorization.context import Context, ResourceRef, ResourceType
from app.core.authorization.permissions import Permissions
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.workflow.domain.context import TriggerContext, normalize_node_output
from app.modules.workflow.domain.events import WorkflowRunTerminalEvent
from app.modules.workflow.domain.errors import (
    WorkflowConflictError,
    WorkflowValidationError,
)
from app.modules.workflow.domain.schema_template import defaults_from_schema
from app.modules.workflow.domain.workflow import WorkflowEntity
from app.modules.workflow.domain.run import (
    TERMINAL_STATUSES,
    WorkflowRunEntity,
    WorkflowRunStatus,
)
from app.modules.workflow.domain.wait import (
    WaitRequest,
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.execution.form_submission import (
    FormNodeMismatchError,
    check_assignee,
    validate_form_inputs,
)
from app.modules.workflow.execution.stepper import RunStepper, StepResult
from app.composition.workflow_agent import AgentControlAdapter
from app.composition.workflow_function import FunctionControlAdapter
from app.composition.workflow_notifications import WorkflowNotificationAdapter
from app.composition.workflow_scheduler import ScheduleControlAdapter
from app.modules.workflow.execution.underlying_work import (
    stop_underlying_work,
    stop_underlying_work_for_wait,
)
from app.modules.workflow.infrastructure.run_channel import publish_run_state
from app.modules.workflow.infrastructure.repositories import (
    SqlAlchemyWorkflowRepository,
    SqlAlchemyWorkflowRunRepository,
    SqlAlchemyWorkflowRunWaitRepository,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


class WorkflowEngine:
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        agent_adapter=None,
        function_adapter=None,
        schedule_adapter=None,
        notification_adapter=None,
    ):
        self.uow = uow
        self.flow_repo = SqlAlchemyWorkflowRepository(uow)
        self.run_repo = SqlAlchemyWorkflowRunRepository(uow)
        self.wait_repo = SqlAlchemyWorkflowRunWaitRepository(uow)

        self.agent_adapter = agent_adapter or AgentControlAdapter(uow)
        self.function_adapter = function_adapter or FunctionControlAdapter(uow)
        self.schedule_adapter = schedule_adapter or ScheduleControlAdapter(uow)
        self.notification_adapter = notification_adapter or WorkflowNotificationAdapter(
            uow
        )

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
        run_id: UUID | None = None,
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

        if run_id is not None:
            existing = await self.run_repo.get(run_id)
            if existing is not None:
                self._validate_reserved_run(existing, flow_id=flow.id, user_id=user_id)
                return existing

        if schedule_event_id is not None:
            existing = await self.run_repo.find_by_schedule_event(
                flow_id=flow.id,
                user_id=user_id,
                schedule_event_id=schedule_event_id,
            )
            if existing is not None:
                return existing

        run = WorkflowRunEntity.create(
            run_id=run_id,
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
            if run_id is None:
                await self.run_repo.create(run)
            else:
                async with self.uow.session.begin_nested():
                    await self.run_repo.create(run)
        except IntegrityError as exc:
            if run_id is not None:
                existing = await self.run_repo.get(run_id)
                if existing is not None:
                    self._validate_reserved_run(
                        existing, flow_id=flow.id, user_id=user_id
                    )
                    return existing
            if schedule_event_id is not None:
                existing = await self.run_repo.find_by_schedule_event(
                    flow_id=flow.id,
                    user_id=user_id,
                    schedule_event_id=schedule_event_id,
                )
                if existing is not None:
                    return existing
            raise WorkflowConflictError("Workflow run identity already exists") from exc

        result = await self._stepper(ctx).advance(run, flow)

        # create() persisted only the entry-node state; write the post-advance state
        # (final node, status, step history, context) before committing.
        run = await self.run_repo.update(run)
        pending_wait = await self._persist_wait(run, result)
        self._collect_terminal_event(run)
        await self.uow.commit()
        await self._announce(run)
        if pending_wait is not None:
            await self._announce_human_wait(run, pending_wait)

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
        await check_assignee(self.uow, wait, run.pod_id, requester_user_id)

        # The resolved schema rides on the wait. Fill omitted fields from
        # schema defaults (user submission wins), then validate the merged
        # values against that schema so dynamic enums are enforced server-side.
        schema = wait.payload.get("input_schema")
        merged = {**defaults_from_schema(schema), **dict(inputs)}
        validate_form_inputs(node_id, schema, merged)

        wait.complete(merged)
        await self.wait_repo.update(wait)
        # Close the inbox entry in the same breath as the wait, so the two never
        # disagree about whether this step is still owed.
        await self.notification_adapter.close_form_notification(
            pod_id=run.pod_id,
            run_id=run.id,
            node_id=node_id,
            summary="Form submitted.",
            data=merged,
        )

        run.resume(node_id, merged)
        result = await self._stepper(ctx).continue_after(run, flow, node_id)

        run = await self.run_repo.update(run)
        pending_wait = await self._persist_wait(run, result)
        self._collect_terminal_event(run)
        await self.uow.commit()
        await self._announce(run)
        if pending_wait is not None:
            await self._announce_human_wait(run, pending_wait)
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
        pending_wait = await self._persist_wait(run, result)
        self._collect_terminal_event(run)
        await self.uow.commit()
        await self._announce(run)
        if pending_wait is not None:
            await self._announce_human_wait(run, pending_wait)
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
        self._collect_terminal_event(run)
        await self.uow.commit()
        await self._announce(run)
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
            await stop_underlying_work(
                wait,
                run=run,
                agent_adapter=self.agent_adapter,
                function_adapter=self.function_adapter,
            )
        # A cancelled run must not leave a question sitting in someone's inbox
        # waiting for an answer that nobody will ever read.
        await self.notification_adapter.cancel_for_run(run_id=run.id)
        try:
            run.cancel()
        except ValueError as exc:
            raise WorkflowConflictError(str(exc)) from exc
        run = await self.run_repo.update(run)
        self._collect_terminal_event(run)
        await self.uow.commit()
        await self._announce(run)
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

    @staticmethod
    def _validate_reserved_run(
        run: WorkflowRunEntity,
        *,
        flow_id: UUID,
        user_id: UUID,
    ) -> None:
        if run.flow_id != flow_id or run.user_id != user_id:
            raise WorkflowConflictError(
                "Reserved workflow run ID belongs to a different target or owner"
            )

    def _collect_terminal_event(self, run: WorkflowRunEntity) -> None:
        if run.status not in TERMINAL_STATUSES:
            return
        self.uow.collect_events([WorkflowRunTerminalEvent.from_run(run)])

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

    async def _announce(self, run: WorkflowRunEntity) -> None:
        """Push the committed run to anyone streaming it.

        Called only after `uow.commit()` — a subscriber must never observe state
        the database has not accepted. Imported lazily to keep the API schema
        module out of the engine's import cycle.
        """
        from app.modules.workflow.api.schemas import run_response_from_domain

        # Everything here is best effort and must stay that way: the run is
        # already committed, so a failure to *tell anyone* cannot be allowed to
        # turn a successful advance into a raised exception. Clients still poll.
        try:
            wait = await self.wait_repo.get_active_for_run(run.id)
            payload = run_response_from_domain(run, wait).model_dump(mode="json")
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

    async def stop_underlying_work(self, wait: WorkflowRunWaitEntity) -> None:
        """Stop the work a wait holds, for a caller that has no run in hand."""
        await stop_underlying_work_for_wait(
            wait,
            run_repo=self.run_repo,
            agent_adapter=self.agent_adapter,
            function_adapter=self.function_adapter,
        )

    async def _persist_wait(
        self, run: WorkflowRunEntity, result: StepResult
    ) -> WorkflowRunWaitEntity | None:
        """Write the wait row. Telling the assignee happens after the commit.

        It used to happen here, which put a Slack/Teams/WhatsApp/email send
        inside the run transaction and under the run's row lock — for the
        seconds a platform API can take, with a pooled connection held. The
        cheap Redis announce next door already had the right sequencing, and
        says why in its own docstring; the expensive one had the opposite.
        """
        if result.wait is None or run.status not in (
            WorkflowRunStatus.WAITING,
            WorkflowRunStatus.RUNNING,
        ):
            return None
        assert run.current_node_id is not None
        return await self.wait_repo.create(self._wait_entity(run, result.wait))

    async def _announce_human_wait(
        self, run: WorkflowRunEntity, wait: WorkflowRunWaitEntity
    ) -> None:
        """Tell the assignee a form is waiting on them.

        Until this existed the wait was a pure pull queue: the row was written,
        the "waiting for you" list rendered it, and nobody was ever told. A
        workflow could sit for days on someone who had no idea it existed.

        Unassigned waits are skipped on purpose — there is no one person to tell,
        and broadcasting to the pod would make every form everyone's problem.
        """
        if (
            wait.wait_type is not WorkflowRunWaitType.HUMAN
            or wait.assigned_pod_member_id is None
        ):
            return
        flow = await self.flow_repo.get(run.flow_id)
        await self.notification_adapter.notify_form_assignee(
            pod_id=run.pod_id,
            run_id=run.id,
            flow_id=run.flow_id,
            node_id=wait.node_id,
            assigned_pod_member_id=wait.assigned_pod_member_id,
            flow_name=getattr(flow, "name", None),
            schema=wait.payload.get("input_schema"),
            actor_user_id=run.user_id,
        )

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
            # Kept in `payload` too: the reconcile sweep still reads it from
            # there, and older rows have only that copy.
            scheduled_at=request.scheduled_at,
            payload=payload,
        )
