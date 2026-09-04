"""Functions, as an agent's tools and its context brief use them.

Four operations, replacing the three factories in
`app/composition/agent_functions.py`. Those handed `agent` two repositories and
a `FunctionUseCases`, of which it called four methods -- so every other method
on all three was part of what `agent` could reach, and moving any of them broke
the agent's tool factory.

Each takes the caller's unit of work or factory rather than opening its own: an
agent tool runs inside a live session and these are reads and a dispatch it
wants inside that session, unlike a notification send.

A submodule rather than `contracts/__init__`, which is a leaf: these reach the
repository and application layers.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.function.domain.entities import FunctionEntity, FunctionRunEntity


async def get_function_by_id(uow, function_id: UUID) -> FunctionEntity | None:
    """One function, or ``None`` when the id names nothing.

    ``None`` rather than a raise: the caller is turning an agent's grants into
    tools, and a grant naming a function that has since been deleted should cost
    that one tool rather than the agent's whole toolset.
    """
    from app.modules.function.infrastructure.repositories import FunctionRepository

    return await FunctionRepository(uow).get(function_id)


async def list_pod_functions(
    uow, pod_id: UUID, *, limit: int
) -> tuple[list[FunctionEntity], str | None]:
    """This pod's functions and a cursor, for a listing the agent is shown."""
    from app.modules.function.infrastructure.repositories import FunctionRepository

    return await FunctionRepository(uow).list_by_pod(pod_id, limit=limit)


async def get_function_run(uow, run_id: UUID) -> FunctionRunEntity | None:
    """One run, for a caller waiting on a JOB function to reach a terminal state."""
    from app.modules.function.infrastructure.repositories import FunctionRunRepository

    return await FunctionRunRepository(uow).get_run(run_id)


async def execute_function_for_agent(
    uow_factory,
    *,
    pod_id: UUID,
    name: str,
    input_data: dict[str, object],
    user_id: UUID,
    agent_id: UUID,
    agent_name: str | None,
    delegation_scope,
) -> FunctionRunEntity:
    """Run a function on an agent's behalf, under the function's own identity.

    ``run_as_workload`` is deliberately not a parameter. The function executes
    as its own FUNCTION principal with its own grants -- the same identity as
    the direct-user and JOB paths -- so exposing one as an agent tool needs
    exactly one grant on the parent agent, and the function's resource grants
    are never mirrored onto it. A caller able to pass a workload could undo that.
    """
    from app.modules.function.api.dependencies import build_function_use_cases

    return await build_function_use_cases(uow_factory).execute_function_as_workload(
        pod_id=pod_id,
        name=name,
        input_data=input_data,
        user_id=user_id,
        principal_type="AGENT",
        principal_id=agent_id,
        delegation_scope=delegation_scope,
        delegation_actor_name=agent_name,
    )


__all__ = [
    "execute_function_for_agent",
    "get_function_by_id",
    "get_function_run",
    "list_pod_functions",
]
