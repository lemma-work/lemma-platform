"""Function execution adapter for workflow nodes."""

from typing import Any
from uuid import UUID

from app.core.authorization.context import Context
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.function.api.dependencies import build_function_use_cases
from app.modules.function.domain.entities import FunctionType
from app.modules.function.infrastructure.repositories import FunctionRunRepository
from app.modules.workflow.domain.ports import FunctionPort


class FunctionControlAdapter(FunctionPort):
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.run_repository = FunctionRunRepository(uow)
        self._use_cases = build_function_use_cases(
            SessionUnitOfWorkFactory(async_session_maker)
        )

    async def execute_function(
        self,
        function_name: str,
        inputs: dict[str, Any],
        pod_id: UUID,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> Any:
        del ctx
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

    async def get_run_status(self, function_run_id: UUID) -> dict[str, Any]:
        run = await self.run_repository.get_run(function_run_id)
        if run is None:
            return {"status": "NOT_FOUND"}
        status = str(getattr(run.status, "value", run.status))
        if status == "COMPLETED":
            return {"status": "COMPLETED", "output_data": run.output_data or {}}
        if status == "FAILED":
            return {
                "status": "FAILED",
                "error": run.error or "Function run failed",
                "output_data": run.output_data or {},
            }
        return {"status": "RUNNING"}
