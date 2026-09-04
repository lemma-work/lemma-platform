"""What another module does to a pod's functions when it builds one.

Three operations, not `FunctionService`. Two of them are the two halves of the
service's ``raise_not_found`` flag, given names: a caller either asks whether a
function is there yet (the bundle applier, ordering a deferred grant step) or
requires the one it just listed (the exporter). A boolean argument made those
the same operation, and the answer's type depended on it.

A submodule rather than `contracts/__init__`: this reaches the service layer,
and `contracts/__init__` is imported by anything that wants any contract at all.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.modules.function.api.dependencies import build_function_service
from app.modules.function.domain.entities import FunctionEntity


async def list_function_names(
    uow, *, pod_id: UUID, user_id: UUID, ctx: Context
) -> list[str]:
    """Every function in the pod this reader may see."""
    functions, _ = await build_function_service(uow).list_functions(
        pod_id, user_id, limit=1000, ctx=ctx
    )
    return [str(function.name or "") for function in functions]


async def get_function(
    uow,
    *,
    pod_id: UUID,
    name: str,
    user_id: UUID,
    ctx: Context,
    include_code: bool = True,
) -> FunctionEntity | None:
    """The named function, or ``None`` when the pod does not have one."""
    return await build_function_service(uow).get_function_by_name(
        pod_id, name, user_id, include_code=include_code, ctx=ctx
    )


async def require_function(
    uow, *, pod_id: UUID, name: str, user_id: UUID, ctx: Context
) -> FunctionEntity:
    """The named function with its code, raising ``FunctionNotFoundError``."""
    return await build_function_service(uow).get_function_by_name(
        pod_id, name, user_id, raise_not_found=True, ctx=ctx
    )


__all__ = ["get_function", "list_function_names", "require_function"]
