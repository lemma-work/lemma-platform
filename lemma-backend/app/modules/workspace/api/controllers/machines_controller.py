"""`/machines`: what machines a caller has, and their lifecycle.

Every route names a machine by ref and goes through `MachineResolver`; see
`machine_dependencies`. Processes, files, previews and the browser have
controllers of their own.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.log.log import get_logger
from app.modules.workspace.api.controllers.machine_dependencies import (
    ManageMachine,
    SeeMachine,
    UowFactoryDep,
    acting_user,
    caller_for,
)
from app.modules.workspace.api.machine_schemas import (
    CreatePodMachineRequest,
    MachineGrantsModel,
    MachineListResponse,
    MachinePolicyResponse,
    MachineResponse,
    MachineStartupResponse,
    ResetRequest,
    ResetResponse,
    ResetScopeResult,
    SizeOffer,
    UpdatePodMachineRequest,
    machine_response,
)
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine import (
    MachineKind,
    MachineSize,
    MachineTemplate,
    MachineVisibility,
)
from app.modules.workspace.domain.machine_access import (
    CallerKind,
    MachineAccess,
    PodStanding,
)
from app.modules.workspace.domain.machine_errors import (
    MachineAccessDenied,
    MachineNotFound,
    MachinePolicyRefused,
)
from app.modules.workspace.services.machine_lifecycle import (
    AGENT_SCOPES,
    MachineLifecycle,
)
from app.modules.workspace.services.machine_policy import policy_for_user
from app.modules.workspace.services.machine_resolver import MachineResolver
from app.modules.workspace.services.pod_machines import (
    MachineGrants,
    create_pod_machine,
    pod_policy,
    read_grants,
    require_pod_role,
    set_grants,
    update_pod_machine,
)

logger = get_logger(__name__)


def machines_enabled() -> None:
    """`MACHINES_API_ENABLED` off: every machine route is absent, not broken."""
    if not workspace_settings.machines_api_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


router = APIRouter(tags=["Machines"], dependencies=[Depends(machines_enabled)])


async def _units(uow_factory, machine_ids: list[UUID]) -> dict[UUID, float]:
    from app.modules.usage.contracts.compute import compute_units_by_machine

    async with uow_factory() as uow:
        return await compute_units_by_machine(uow, machine_ids)


@router.get(
    "/machines",
    response_model=MachineListResponse,
    operation_id="machine.list",
    summary="List the machines you can see",
)
async def list_machines(
    request: Request,
    uow_factory: UowFactoryDep,
    pod_id: UUID | None = Query(
        default=None, description="Also list this pod's machines."
    ),
) -> MachineListResponse:
    from app.core.app_pin import app_pod_for

    pod_id = app_pod_for(request, pod_id)
    async with uow_factory() as uow:
        caller = await caller_for(request, uow, pod_id=pod_id)
        pod_ids = [pod_id] if pod_id else ([caller.pod_id] if caller.pod_id else [])
        visible = await MachineResolver(uow).visible(caller, pod_ids=pod_ids)
        await uow.commit()
    units = await _units(uow_factory, [r.machine.id for r in visible])
    return MachineListResponse(
        machines=[
            machine_response(r.machine, r.access, compute_units=units.get(r.machine.id))
            for r in visible
        ]
    )


@router.get(
    "/machines/{ref}",
    response_model=MachineResponse,
    operation_id="machine.get",
    summary="Get one machine",
)
async def get_machine(
    resolved: SeeMachine, uow_factory: UowFactoryDep
) -> MachineResponse:
    units = await _units(uow_factory, [resolved.machine.id])
    return machine_response(
        resolved.machine, resolved.access, compute_units=units.get(resolved.machine.id)
    )


@router.get(
    "/machines/{ref}/startup",
    response_model=MachineStartupResponse,
    response_model_exclude_none=True,
    operation_id="machine.startup",
    summary="Whether a machine is ready, without waking it",
)
async def machine_startup(resolved: SeeMachine) -> MachineStartupResponse:
    from app.modules.workspace.services.sandbox_composition import get_sandbox_service
    from app.modules.workspace.services.sandbox_progress import current_phase
    from sandbox_runtime.errors import SandboxError

    service = get_sandbox_service()
    try:
        phase = await current_phase(resolved.machine.id)
        if phase is not None:
            return MachineStartupResponse(
                state=phase["phase"],
                detail=phase["detail"],
                done_mb=phase.get("done_mb"),
                total_mb=phase.get("total_mb"),
            )
        info = await service.describe(resolved.machine.id)
    except (SandboxError, OSError) as exc:
        logger.warning(
            "workspace.machines.startup_unavailable.degraded",
            error_type=type(exc).__name__,
        )
        return MachineStartupResponse(state="unavailable")
    if info is not None and info.status == "RUNNING":
        return MachineStartupResponse(state="ready")
    return MachineStartupResponse(state="asleep")


@router.post(
    "/machines/{ref}:release",
    response_model=MachineResponse,
    operation_id="machine.release",
    summary="Stop a machine's compute, keeping its disk",
)
async def release_machine(
    request: Request, resolved: ManageMachine, uow_factory: UowFactoryDep
) -> MachineResponse:
    await MachineLifecycle(uow_factory).release(
        resolved.machine, acting_user=acting_user(request, resolved)
    )
    return await _fresh(uow_factory, resolved.machine.id, resolved.access)


@router.post(
    "/machines/{ref}:restart",
    response_model=MachineResponse,
    operation_id="machine.restart",
    summary="Stop a machine and start it again",
)
async def restart_machine(
    request: Request, resolved: ManageMachine, uow_factory: UowFactoryDep
) -> MachineResponse:
    await MachineLifecycle(uow_factory).restart(
        resolved.machine, acting_user=acting_user(request, resolved)
    )
    return await _fresh(uow_factory, resolved.machine.id, resolved.access)


@router.post(
    "/machines/{ref}:reset",
    response_model=ResetResponse,
    operation_id="machine.reset",
    summary="Clean a machine by scope",
)
async def reset_machine(
    request: Request,
    body: ResetRequest,
    uow_factory: UowFactoryDep,
    ref: str,
) -> ResetResponse:
    from app.modules.workspace.api.controllers.machine_dependencies import (
        resolve_for_request,
    )

    # Agents may clean the machine they use, within the scopes that cannot
    # cost anybody anything they did not make; everything else is the
    # machine's managers'.
    caller_kind = await _caller_kind(request, uow_factory)
    agent_only = set(body.scopes) <= AGENT_SCOPES
    need = (
        MachineAccess.USE
        if caller_kind is CallerKind.AGENT and agent_only
        else MachineAccess.MANAGE
    )
    resolved, _caller = await resolve_for_request(request, uow_factory, ref, need=need)
    request.state.machine_caller = _caller
    if caller_kind is CallerKind.AGENT and not agent_only:
        raise MachineAccessDenied(ref, "reset that scope of")
    outcomes = await MachineLifecycle(uow_factory).reset(
        resolved.machine,
        list(body.scopes),
        acting_user=acting_user(request, resolved),
        confirm=body.confirm,
        forget_logins=body.forget_logins,
    )
    return ResetResponse(
        results=[
            ResetScopeResult(
                scope=o.scope,  # type: ignore[arg-type]
                freed_bytes=o.freed_bytes,
                detail=o.detail,
            )
            for o in outcomes
        ]
    )


async def _caller_kind(request: Request, uow_factory) -> CallerKind:
    async with uow_factory() as uow:
        return (await caller_for(request, uow)).kind


async def _fresh(
    uow_factory, machine_id: UUID, access: MachineAccess
) -> MachineResponse:
    from app.modules.workspace.infrastructure.machine_repository import (
        MachineRepository,
    )

    async with uow_factory() as uow:
        machine = await MachineRepository(uow).get(machine_id)
    if machine is None:
        raise MachineNotFound(str(machine_id))
    return machine_response(machine, access)


# --- policy --------------------------------------------------------------------


@router.get(
    "/machines:policy",
    response_model=MachinePolicyResponse,
    operation_id="machine.policy",
    summary="Which machines you or a pod may create",
)
async def machine_policy(
    request: Request,
    uow_factory: UowFactoryDep,
    pod_id: UUID | None = Query(default=None),
) -> MachinePolicyResponse:
    async with uow_factory() as uow:
        caller = await caller_for(request, uow)
        if pod_id is not None:
            if caller.user_id is None:
                raise MachineNotFound(f"pod:{pod_id}")
            await require_pod_role(uow, pod_id, caller.user_id, PodStanding.VIEWER)
            policy = await pod_policy(uow, pod_id)
        elif caller.user_id is not None:
            policy = await policy_for_user(uow, caller.user_id)
        else:
            raise MachineNotFound("me")
    return MachinePolicyResponse(
        default_size=policy.default_size.label,
        templates=[
            SizeOffer(
                template=template.value,  # type: ignore[arg-type]
                sizes=[size.label for size in policy.sizes_for(template)],
            )
            for template in MachineTemplate
            if policy.sizes_for(template)
        ],
        max_pod_machines=policy.max_pod_machines,
        max_connected_machines=policy.max_connected_machines,
        max_running=policy.max_running,
    )


# --- pod machines --------------------------------------------------------------


def _size(label: str | None) -> MachineSize | None:
    if label is None:
        return None
    try:
        return MachineSize.parse(label)
    except ValueError as exc:
        raise MachinePolicyRefused(str(exc), code="MACHINE_SIZE_NOT_OFFERED") from None


@router.post(
    "/pods/{pod_id}/machines",
    response_model=MachineResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="machine.pod.create",
    summary="Create a machine the pod's agents share",
)
async def create_machine(
    pod_id: UUID,
    body: CreatePodMachineRequest,
    request: Request,
    uow_factory: UowFactoryDep,
) -> MachineResponse:
    async with uow_factory() as uow:
        caller = await caller_for(request, uow, pod_id=pod_id)
        if caller.kind is not CallerKind.USER or caller.user_id is None:
            raise MachinePolicyRefused(
                "Pod machines are created by people, not by apps or agents.",
                code="MACHINE_EDITOR_REQUIRED",
            )
        machine = await create_pod_machine(
            uow,
            pod_id=pod_id,
            user_id=caller.user_id,
            name=body.name,
            display_name=body.display_name,
            template=MachineTemplate(body.template),
            size=_size(body.size),
            visibility=MachineVisibility(body.visibility),
            run_slots=body.run_slots,
        )
        await uow.commit()
    return machine_response(machine, MachineAccess.MANAGE)


async def _pod_machine(request: Request, uow, pod_id: UUID, name: str):
    caller = await caller_for(request, uow, pod_id=pod_id)
    resolved = await MachineResolver(uow).resolve(
        f"pod:{pod_id}:{name}", caller, need=MachineAccess.MANAGE
    )
    if resolved.machine.kind is not MachineKind.POD:
        raise MachineNotFound(name)
    return caller, resolved


@router.patch(
    "/pods/{pod_id}/machines/{name}",
    response_model=MachineResponse,
    operation_id="machine.pod.update",
    summary="Rename, resize or change who sees a pod machine",
)
async def update_machine(
    pod_id: UUID,
    name: str,
    body: UpdatePodMachineRequest,
    request: Request,
    uow_factory: UowFactoryDep,
) -> MachineResponse:
    async with uow_factory() as uow:
        caller, resolved = await _pod_machine(request, uow, pod_id, name)
        assert caller.user_id is not None
        machine = await update_pod_machine(
            uow,
            resolved.machine,
            user_id=caller.user_id,
            display_name=body.display_name,
            size=_size(body.size),
            visibility=MachineVisibility(body.visibility) if body.visibility else None,
            run_slots=body.run_slots,
        )
        await uow.commit()
    return machine_response(machine, resolved.access)


@router.delete(
    "/pods/{pod_id}/machines/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="machine.pod.delete",
    summary="Delete a pod machine and its disk",
)
async def delete_machine(
    pod_id: UUID, name: str, request: Request, uow_factory: UowFactoryDep
) -> None:
    async with uow_factory() as uow:
        _caller, resolved = await _pod_machine(request, uow, pod_id, name)
        await uow.commit()
    await MachineLifecycle(uow_factory).delete(resolved.machine)


@router.get(
    "/pods/{pod_id}/machines/{name}/grants",
    response_model=MachineGrantsModel,
    operation_id="machine.pod.grants.get",
    summary="Who is granted a pod machine",
)
async def get_machine_grants(
    pod_id: UUID, name: str, request: Request, uow_factory: UowFactoryDep
) -> MachineGrantsModel:
    async with uow_factory() as uow:
        _caller, resolved = await _pod_machine(request, uow, pod_id, name)
        grants = await read_grants(uow, resolved.machine)
    return MachineGrantsModel(
        agents=list(grants.agents),
        functions=list(grants.functions),
        members=list(grants.members),
    )


@router.put(
    "/pods/{pod_id}/machines/{name}/grants",
    response_model=MachineGrantsModel,
    operation_id="machine.pod.grants.set",
    summary="Replace who is granted a pod machine",
)
async def set_machine_grants(
    pod_id: UUID,
    name: str,
    body: MachineGrantsModel,
    request: Request,
    uow_factory: UowFactoryDep,
) -> MachineGrantsModel:
    async with uow_factory() as uow:
        caller, resolved = await _pod_machine(request, uow, pod_id, name)
        assert caller.user_id is not None
        grants = await set_grants(
            uow,
            resolved.machine,
            MachineGrants(
                agents=tuple(body.agents),
                functions=tuple(body.functions),
                members=tuple(body.members),
            ),
            created_by=caller.user_id,
        )
        await uow.commit()
    return MachineGrantsModel(
        agents=list(grants.agents),
        functions=list(grants.functions),
        members=list(grants.members),
    )
