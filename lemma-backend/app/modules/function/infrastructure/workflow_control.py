"""Running a pod's functions on a workflow's behalf.

The adapter behind `workflow`'s `FunctionPort`. It lived in
`app/composition/workflow_function.py`, which is how a function node came to put
two of this module's internal paths -- `function.api.dependencies` and
`function.infrastructure.repositories` -- into another module's build. Neither
was workflow's to name: dispatching a run is one operation, and how the use
cases and the run repository are assembled is this module's business.

Everything here happens in the workflow's own transaction and touches nothing
but the database: the run row and the ``function.run`` item that starts it are
written with the wait the workflow parks on, and a cancel ends the run in the
same transaction that stops waiting for it. That is what lets a workflow step
advance inside a FAST work handler, and it is why a crash can no longer leave a
committed wait whose function run was never queued.

Here rather than in `workflow` for the same reason `AgentControlAdapter` sits in
`agent`: it is the provider's implementation of the consumer's port, published
as a factory through `function/contracts/workflow_control.py`.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.scope import context_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionRunStatus,
    FunctionType,
)
from app.modules.function.domain.errors import FunctionValidationError
from app.modules.function.infrastructure.execution_repository import (
    FunctionExecutionRepository,
)
from app.modules.function.infrastructure.repositories import FunctionRunRepository
from app.modules.function.services.execution_preflight import (
    LegacyFunctionRevisionRequired,
)
from app.modules.workflow.contracts import FunctionPort

#: What a run cancelled because its workflow stopped waiting says about it.
_NOBODY_WAITING = "Cancelled: the workflow step waiting on this run stopped waiting."


class FunctionControlAdapter(FunctionPort):
    """Dispatch, cancel and read back one function run for a workflow node."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._uow = uow
        self._runs = FunctionRunRepository(uow)

    async def execute_function(
        self,
        function_name: str,
        inputs: dict[str, object],
        pod_id: UUID,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> dict[str, object]:
        from app.modules.function.api.dependencies import build_function_service

        del ctx
        # The run's owner, as of now: the step may run long after the person
        # who started the workflow was last checked.
        auth_ctx = await AuthorizationDataService(self._uow.session).build_user_context(
            user_id=user_id, pod_id=pod_id
        )
        try:
            async with context_scope(auth_ctx):
                resolved = await build_function_service(self._uow).resolve_execute(
                    pod_id,
                    function_name,
                    inputs,
                    user_id,
                    None,
                    ctx=auth_ctx,
                    dispatch_mode=FunctionDispatchMode.ASYNCHRONOUS,
                )
        except LegacyFunctionRevisionRequired as exc:
            # Upgrading one compiles it in a sandbox, which a workflow step --
            # one database transaction -- cannot do. Running the function once
            # directly, or saving it, upgrades it.
            raise FunctionValidationError(
                f"Function '{function_name}' predates executable revisions. Run "
                "it once directly, or save it, and then retry this step."
            ) from exc
        run = resolved.run
        return {
            "run_id": str(run.id),
            "status": FunctionRunStatus(run.status).value,
            "function_type": FunctionType.JOB.value,
        }

    async def cancel_run(self, function_run_id: UUID) -> None:
        """Cancel the run in this transaction. A finished run is left alone."""
        await FunctionExecutionRepository(self._uow).cancel_open(
            function_run_id, error=_NOBODY_WAITING
        )

    async def get_run_status(self, function_run_id: UUID) -> dict[str, object]:
        run = await self._runs.get_run(function_run_id)
        if run is None:
            return {"status": "NOT_FOUND"}
        status = str(getattr(run.status, "value", run.status))
        if status == "COMPLETED":
            return {"status": "COMPLETED", "output_data": run.output_data or {}}
        if status in {"FAILED", "CANCELLED"}:
            return {
                "status": "FAILED",
                "error": run.error or "Function run failed",
                "output_data": run.output_data or {},
            }
        return {"status": "RUNNING"}
