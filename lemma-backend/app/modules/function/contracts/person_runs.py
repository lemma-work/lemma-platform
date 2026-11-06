"""Functions, as an outside MCP client runs them for the person it acts as.

Two operations for the pod's MCP surface (``agent/services/pod_mcp_functions``):
which functions the person may run, and running one. The person runs it
through the pod's own assistant -- the standing a framed app's token holds and
the assistant's own CLI calls run with -- so their access decides whether it
may run, and the function's own grants decide what it touches, exactly as for
a run started in Lemma.

A submodule rather than `contracts/__init__`, which is a leaf: these reach the
repository and application layers.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRunEntity,
    FunctionStatus,
)


async def runnable_functions(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, ctx: Context, limit: int
) -> list[FunctionEntity]:
    """The ready functions in the pod ``ctx`` may run, by name.

    Read in one page of ``limit``: a caller showing these as tools caps them
    anyway, and a listing that pages would be a tool list that changes under
    the host between two requests.
    """
    from app.modules.function.infrastructure.repositories import FunctionRepository

    visible, _ = await FunctionRepository(uow).list_visible_by_pod(pod_id, ctx, limit)
    return sorted(
        (
            function
            for function in visible
            if function.status is FunctionStatus.READY
            and Permissions.FUNCTION_EXECUTE in function.allowed_actions
        ),
        key=lambda function: function.name.casefold(),
    )


async def run_function_as_person(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    name: str,
    input_data: dict[str, object],
    user_id: UUID,
) -> FunctionRunEntity:
    """Run ``name`` for the person, through the pod's own assistant.

    The assistant's id is the pod's, and it carries no actor name: any other
    name demotes the context to a named workload holding only its own grants.
    """
    from app.modules.function.api.dependencies import build_function_use_cases

    return await build_function_use_cases(uow_factory).execute_function_as_workload(
        pod_id=pod_id,
        name=name,
        input_data=input_data,
        user_id=user_id,
        principal_type="AGENT",
        principal_id=pod_id,
        delegation_scope=None,
        delegation_actor_name=None,
        is_default_pod_agent=True,
    )


__all__ = ["run_function_as_person", "runnable_functions"]
