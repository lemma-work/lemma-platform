"""Function adapter for the workflow module."""

from typing import Any, Dict
from uuid import UUID

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.function.domain.entities import FunctionType
from app.modules.workflow.domain.ports import FunctionPort


class FunctionControlAdapter(FunctionPort):
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        # A short-UoW read repo for run-status reconciliation.
        from app.modules.function.infrastructure.repositories import (
            FunctionRunRepository,
        )

        self.run_repository = FunctionRunRepository(uow)
        # The function use case scopes its own short UoWs (it must not hold the
        # workflow's pooled connection across the sandbox round-trip), so build it
        # from a session factory rather than the workflow's bound uow.
        from app.core.infrastructure.db.session import async_session_maker
        from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
        from app.modules.function.api.dependencies import build_function_use_cases

        self._use_cases = build_function_use_cases(
            SessionUnitOfWorkFactory(async_session_maker)
        )

    async def execute_function(
        self,
        function_name: str,
        inputs: Dict[str, Any],
        pod_id: UUID,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> Any:
        # Dispatch-and-suspend for ALL function types (not just JOB). The run is
        # enqueued to the worker and returned PENDING; the workflow engine
        # suspends on the run id, committing (and releasing its run-row lock +
        # pooled connection) at the suspend boundary in milliseconds instead of
        # holding them across the function's sandbox round-trip. The worker
        # executes the run and its FunctionRunCompleted event resumes the workflow
        # via resume_workflow_run_for_function (the same path JOB functions use).
        run = await self._use_cases.dispatch_function_for_workflow(
            pod_id=pod_id,
            name=function_name,
            input_data=inputs,
            user_id=user_id,
        )
        return {
            "run_id": str(run.id),
            "status": str(getattr(run.status, "value", run.status)),
            "function_type": FunctionType.JOB.value,
        }

    async def get_run_status(self, function_run_id: UUID) -> Dict[str, Any]:
        """Status/output of a function run, for completion reconciliation."""
        run = await self.run_repository.get_run(function_run_id)
        if run is None:
            return {"status": "NOT_FOUND"}
        status = str(run.status.value if hasattr(run.status, "value") else run.status)
        if status == "COMPLETED":
            return {"status": "COMPLETED", "output_data": run.output_data or {}}
        if status == "FAILED":
            return {
                "status": "FAILED",
                "error": run.error or "Function run failed",
                "output_data": run.output_data or {},
            }
        return {"status": "RUNNING"}
