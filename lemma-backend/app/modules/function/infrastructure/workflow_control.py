"""Running a pod's functions on a workflow's behalf.

The adapter behind `workflow`'s `FunctionPort`. It lived in
`app/composition/workflow_function.py`, which is how a function node came to put
two of this module's internal paths -- `function.api.dependencies` and
`function.infrastructure.repositories` -- into another module's build. Neither
was workflow's to name: dispatching a run is one operation, and how the use
cases and the run repository are assembled is this module's business.

Here rather than in `workflow` for the same reason `AgentControlAdapter` sits in
`agent`: it is the provider's implementation of the consumer's port, published
as a factory through `function/contracts/workflow_control.py`.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.function.api.dependencies import build_function_use_cases
from app.modules.function.domain.entities import FunctionType
from app.modules.function.infrastructure.repositories import FunctionRunRepository
from app.modules.workflow.contracts import FunctionPort


class FunctionControlAdapter(FunctionPort):
    """Dispatch, cancel and read back one function run for a workflow node."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._runs = FunctionRunRepository(uow)
        self._use_cases = build_function_use_cases(
            SessionUnitOfWorkFactory(async_session_maker)
        )

    async def execute_function(
        self,
        function_name: str,
        inputs: dict[str, object],
        pod_id: UUID,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> dict[str, object]:
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

    async def cancel_run(self, function_run_id: UUID) -> None:
        """Cancel the dispatched run. Already-finished runs cancel to a no-op."""
        await self._use_cases.cancel_function_run(function_run_id)

    async def get_run_status(self, function_run_id: UUID) -> dict[str, object]:
        run = await self._runs.get_run(function_run_id)
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
