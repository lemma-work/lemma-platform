"""A function's resource grants, as the API reads and writes them.

Split out of `function_controller.py`, which was over the 600-line ceiling. The
four helpers here are one subject -- reading a grantee's grants into a response,
and writing an inline `permissions` block -- and none of them is a route, so
they move together and the routes stay where they were.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable
from uuid import UUID

from app.core.authorization.grants import (
    apply_inline_workload_grants,
    list_grantee_resource_grants,
)
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.function.api.schemas.function_schemas import (
    FunctionPermissionsResponse,
    FunctionResourcePermissionResponse,
)
from app.modules.function.domain.entities import FunctionEntity
from app.modules.identity.contracts import AuthenticatedUser as UserEntity
from app.modules.workspace.contracts.tooling import (
    invalidate_function_workspace_env_cache,
)


async def apply_function_grants(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    function: FunctionEntity,
    data,
    user: UserEntity,
) -> None:
    """Apply a create/update payload's inline ``permissions`` block.

    Functions accepted this block on neither verb while agents accepted it on
    create, so the same bundle payload wired an agent and silently left a
    function with nothing.

    Its own short unit of work, opened after the code has been applied rather
    than a request-scoped one held across it: the caller's other work needs no
    connection, and `use_cases.create_function` already opens its own for the
    parts that do.

    The invalidation is outside the block, which is a fix and not a tidy-up. In
    the transaction it both held a pooled connection across a Redis round trip
    and ran in the wrong order -- a concurrent reader could repopulate the cache
    from the pre-commit state between the invalidation and the commit, which is
    the hazard `SqlAlchemyUnitOfWork.after_commit` was written for.
    """

    assert function.id is not None
    permissions = getattr(data, "permissions", None)
    if permissions is None:
        # An absent block leaves the grants alone, so there is nothing to open a
        # connection for. `apply_inline_workload_grants` returns False here
        # without touching the session; checking first means the common create
        # -- which names no permissions at all -- checks out no connection.
        return
    function_id = function.id
    await write_then_invalidate(
        uow_factory,
        write=lambda session: apply_inline_workload_grants(
            session,
            pod_id=pod_id,
            grantee_type="FUNCTION",
            grantee_id=function_id,
            permissions=permissions,
            created_by_user_id=user.id,
        ),
        # The sandbox reads its grants from a cached environment, so skipping
        # this leaves the function running with the access it had before.
        invalidate=lambda: invalidate_function_workspace_env_cache(
            pod_id=pod_id,
            function_id=function_id,
        ),
    )


async def write_then_invalidate(
    uow_factory: UnitOfWorkFactory,
    *,
    write: Callable[[Any], Awaitable[bool]],
    invalidate: Callable[[], Awaitable[None]],
) -> None:
    """Commit ``write``, and only then drop what it made stale.

    Both halves are parameters so the order can be asserted without a database
    and without a double: the order *is* the behaviour here, and it was wrong.
    Dropping a cache inside the transaction pins a pooled Postgres connection
    across a Redis round trip, and races -- a concurrent reader can repopulate
    the cache from the pre-commit state before the commit lands. See
    `SqlAlchemyUnitOfWork.after_commit`, which exists for the case where the
    caller cannot close its transaction early like this one can.
    """
    async with uow_factory() as uow:
        applied = await write(uow.session)
    if applied:
        await invalidate()


async def grants_for_functions(
    uow: Any,
    pod_id: UUID,
    functions: list[Any],
    include: list[str],
) -> dict[UUID, list[FunctionResourcePermissionResponse]] | None:
    """Grants for a whole page of functions, or None when not requested."""
    if not any(part.strip().lower() == "permissions" for part in include):
        return None
    from app.core.authorization.grants import list_grants_for_grantees

    ids = [f.id for f in functions if f.id is not None]
    grouped = await list_grants_for_grantees(
        uow.session, pod_id=pod_id, grantee_type="FUNCTION", grantee_ids=ids
    )
    return {
        function_id: [
            FunctionResourcePermissionResponse(
                resource_type=resource_type,
                resource_name=resource_name,
                permission_ids=sorted(set(permission_ids)),
            )
            for (resource_type, resource_name), permission_ids in grants.items()
        ]
        for function_id, grants in grouped.items()
    }


async def function_permissions_response(
    uow,
    *,
    pod_id: UUID,
    function: FunctionEntity,
) -> FunctionPermissionsResponse:
    assert function.id is not None
    grouped = await list_grantee_resource_grants(
        uow.session,
        pod_id=pod_id,
        grantee_type="FUNCTION",
        grantee_id=function.id,
    )
    return FunctionPermissionsResponse(
        function_id=function.id,
        function_name=function.name,
        grants=[
            FunctionResourcePermissionResponse(
                resource_type=resource_type,
                resource_name=resource_name,
                permission_ids=sorted(set(permission_ids)),
            )
            for (resource_type, resource_name), permission_ids in grouped.items()
        ],
    )
