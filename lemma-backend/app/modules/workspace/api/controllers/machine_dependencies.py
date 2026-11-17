"""Who is calling a `/machines` route, and which machine they named.

The caller is read off what the auth middleware already decided -- the
session, a delegated workload's claims, a person-less function run, and the
app ``core/app_pin`` recognised a page as: an app's page calls with its
viewer's session and is held to the app's rule rather than the person's.

The machine is resolved in a unit of work of its own, committed and closed
before the route does anything slow -- every route here goes on to talk to a
sandbox, and no database connection may be held across that.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Path, Request

from app.core.authorization.delegation import DelegationClaims
from app.core.authorization.function_run import FunctionRunClaims

from app.core.api.dependencies import get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.workspace.domain.machine_access import (
    CallerKind,
    MachineAccess,
    MachineCaller,
)
from app.modules.workspace.domain.machine_errors import (
    MachineAccessDenied,
    MachineNotFound,
)
from app.modules.workspace.services.machine_resolver import (
    MachineResolver,
    ResolvedMachine,
)

UowFactoryDep = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]


def caller_from_request(
    request: Request, *, pod_id: UUID | None = None
) -> MachineCaller:
    state = getattr(request, "state", None)
    user = getattr(state, "user", None)
    return caller_from_principal(
        user_id=getattr(user, "id", None),
        delegation_claims=getattr(state, "delegation_claims", None),
        function_run_claims=getattr(state, "function_run_claims", None),
        pod_id=pod_id,
    )


def caller_from_principal(
    *,
    user_id: UUID | None,
    delegation_claims: DelegationClaims | None,
    function_run_claims: FunctionRunClaims | None = None,
    pod_id: UUID | None = None,
) -> MachineCaller:
    """The caller a verified credential names, whichever door it came through.

    One function for the HTTP routes and the machine sockets, so a delegated
    token is the same agent or function on both: a socket that read only the
    token's user id handed a schedule's agent token the person's own ttys.
    ``delegation_claims`` must already have been through
    ``resolve_delegated_principal``.
    """
    if function_run_claims is not None:
        return MachineCaller(
            CallerKind.FUNCTION,
            user_id=None,
            pod_id=function_run_claims.pod_id,
            workload_id=function_run_claims.function_id,
        )
    if user_id is None:
        raise MachineNotFound("me")
    claims = delegation_claims
    if claims is not None:
        kind = (
            CallerKind.FUNCTION
            if str(claims.actor_type.value).upper() == "FUNCTION"
            else CallerKind.AGENT
        )
        return MachineCaller(
            kind,
            user_id=user_id,
            pod_id=claims.pod_id,
            workload_id=claims.actor_id,
            conversation_id=_uuid(claims.session_id),
        )
    return MachineCaller(CallerKind.USER, user_id=user_id, pod_id=pod_id)


def _as_app(caller: MachineCaller, request: Request) -> MachineCaller:
    """The same person, held to the app's rule when an app's page sent this.

    Which app, if any, is ``core/app_pin``'s: it reads the host the browser
    says the request came from, which the page's script cannot choose.
    """
    from app.core.app_pin import app_request_of

    app = app_request_of(request)
    if app is None or caller.kind is not CallerKind.USER:
        return caller
    return MachineCaller(
        CallerKind.APP,
        user_id=caller.user_id,
        pod_id=app.pod_id,
        app_id=app.app_id,
    )


async def caller_for(
    request: Request, uow=None, *, pod_id: UUID | None = None
) -> MachineCaller:
    del uow
    return _as_app(caller_from_request(request, pod_id=pod_id), request)


async def resolve_for_request(
    request: Request,
    uow_factory: UnitOfWorkFactory,
    ref: str,
    *,
    need: MachineAccess,
) -> tuple[ResolvedMachine, MachineCaller]:
    async with uow_factory() as uow:
        caller = await caller_for(request, uow)
        resolved = await MachineResolver(uow).resolve(ref, caller, need=need)
        # The default machine is created by being named; keep the row.
        await uow.commit()
    return resolved, caller


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except TypeError, ValueError:
        return None


MachineRefPath = Annotated[
    str,
    Path(
        description=(
            "Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the "
            "pod in context), `mine:<name>`, or its id."
        ),
        max_length=256,
    ),
]


def _needing(need: MachineAccess):
    async def dependency(
        request: Request, ref: MachineRefPath, uow_factory: UowFactoryDep
    ) -> ResolvedMachine:
        resolved, caller = await resolve_for_request(
            request, uow_factory, ref, need=need
        )
        request.state.machine_caller = caller
        return resolved

    return dependency


SeeMachine = Annotated[ResolvedMachine, Depends(_needing(MachineAccess.STATUS))]
UseMachine = Annotated[ResolvedMachine, Depends(_needing(MachineAccess.USE))]
ManageMachine = Annotated[ResolvedMachine, Depends(_needing(MachineAccess.MANAGE))]


def acting_user(request: Request, resolved: ResolvedMachine) -> UUID:
    """The person a call on this machine acts for.

    Refused for a caller with nobody behind it -- a person-less function run.
    This used to fall back to the machine's creator, which handed such a run
    the creator's full session and the creator's reach into pod files.
    """
    caller: MachineCaller | None = getattr(request.state, "machine_caller", None)
    if caller is not None and caller.user_id is not None:
        return caller.user_id
    raise MachineAccessDenied(str(resolved.machine.id), "act for a person on")
