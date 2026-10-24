"""`/machines/{ref}/processes`: run, watch, type into and stop processes.

A process started here carries its caller's Lemma credentials, so the CLI
inside it acts as them -- which is also why an app needs its viewer's yes
before it may start one. Output is read in pages over HTTP, or live over the
``/stream`` socket, which also takes input and terminal resizes: that socket
is the Environments screen's terminal.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import posixpath
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field

from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.workspace.api.controllers.machine_dependencies import (
    UowFactoryDep,
    UseMachine,
    acting_user,
)
from app.modules.workspace.api.controllers.machines_controller import machines_enabled
from app.modules.workspace.domain.machine import Machine
from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.process_output import has_stopped
from app.modules.workspace.services.machine_processes import (
    MachineProcesses,
    ProcessRecord,
    ProcessView,
    StartProcess,
)
from sandbox_runtime.paths import HOME_ROOT, WORKSPACE_ROOT
from sandbox_runtime.protocol import ProcessState, TerminalSize

logger = get_logger(__name__)

router = APIRouter(tags=["Machine Processes"], dependencies=[Depends(machines_enabled)])


def machine_processes() -> MachineProcesses:
    return MachineProcesses()


ProcessesDep = Annotated[MachineProcesses, Depends(machine_processes)]


class TerminalSizeModel(BaseModel):
    cols: int = Field(ge=1, le=1000)
    rows: int = Field(ge=1, le=1000)


class StartProcessRequest(BaseModel):
    command: str = Field(min_length=1, max_length=64 * 1024)
    cwd: str | None = Field(
        default=None,
        description="Absolute, inside the machine's home. Defaults to ~/lemma.",
    )
    env: dict[str, str] = Field(default_factory=dict)
    tty: TerminalSizeModel | None = Field(
        default=None, description="Run in a terminal of this size (for a shell)."
    )
    timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        description="How long it may run. Capped at the machine's process lifetime.",
    )
    service: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Run as a named service: no lifetime cap, not stopped when the run "
            "that started it ends, listed under Processes as a service."
        ),
    )
    restart_on_resume: bool = Field(
        default=False, description="With `service`: start it again after a pause."
    )
    run_id: str | None = Field(
        default=None,
        max_length=64,
        description="Stop it when this run ends (pod machines tag agent work this way).",
    )


class ProcessResponse(BaseModel):
    id: str
    command: str
    cwd: str
    state: Literal[
        "reserved",
        "starting",
        "unknown",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
    ]
    exit_code: int | None = None
    started_at: str | None = None
    started_by: UUID | None = None
    tty: bool = False
    service: str | None = None
    run_id: str | None = None


class ProcessListResponse(BaseModel):
    processes: list[ProcessResponse]


class OutputChunkModel(BaseModel):
    sequence: int
    channel: Literal["stdout", "stderr", "pty"]
    data: str = Field(description="UTF-8, with undecodable bytes replaced.")


class ProcessOutputResponse(BaseModel):
    chunks: list[OutputChunkModel]
    next_sequence: int
    state: str
    exit_code: int | None = None
    truncated_before_sequence: int | None = None


class ProcessInputRequest(BaseModel):
    data: str | None = Field(default=None, description="Text to type.")
    data_base64: str | None = Field(default=None, description="Raw bytes, base64.")


def _response(view: ProcessView) -> ProcessResponse:
    record = view.record
    return ProcessResponse(
        id=record.process_id,
        command=record.command,
        cwd=record.cwd,
        state=str(getattr(view.state, "value", view.state)),  # type: ignore[arg-type]
        exit_code=view.exit_code,
        started_at=record.started_at or None,
        started_by=UUID(record.started_by) if record.started_by else None,
        tty=record.tty,
        service=record.service,
        run_id=record.run_id,
    )


def _cwd(machine: Machine, cwd: str | None) -> str:
    """Inside the machine's home: a person's ~/lemma unless they say otherwise.

    A connected machine's home is wherever its owner rooted it, so the check
    is against the machine's home rather than the sandbox one.
    """
    default = WORKSPACE_ROOT if machine.home == HOME_ROOT else f"{machine.home}/lemma"
    target = posixpath.normpath(cwd or default)
    inside = target == machine.home or target.startswith(f"{machine.home}/")
    if not target.startswith("/") or not inside:
        raise MachineConflict(
            f"A process's directory must be inside {machine.home}.",
            code="MACHINE_PATH_OUTSIDE_HOME",
        )
    return target


async def _ready_machine_for(
    machine: Machine, user_id: UUID, cwd: str
) -> dict[str, str]:
    """The machine up, the directory there, and the caller's credentials."""
    from app.modules.workspace.services.workspace_sandbox_service import (
        WorkspaceSandboxService,
    )

    service = WorkspaceSandboxService()
    try:
        session = await service.get_session(
            user_id,
            pod_id=machine.pod_id,
            initial_cwd=cwd,
            close_on_exit=True,
            env_vars={},
            machine_id=machine.id,
        )
        await session.close()
        return await service.get_env_vars(user_id, machine.pod_id)
    finally:
        await service.close()


async def _refuse_when_out_of_compute(uow_factory, machine: Machine) -> None:
    from app.modules.workspace.domain.machine import MachineKind
    from app.modules.workspace.domain.sandbox import SandboxOwnerKind
    from app.modules.workspace.services.compute_meter import refuse_if_exhausted

    if machine.kind is MachineKind.CONNECTED:
        return
    async with uow_factory() as uow:
        await refuse_if_exhausted(
            uow,
            owner_kind=SandboxOwnerKind.POD
            if machine.kind is MachineKind.POD
            else SandboxOwnerKind.USER,
            owner_id=machine.owner_id,
            created_by=machine.created_by,
        )


@router.post(
    "/machines/{ref}/processes",
    response_model=ProcessResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="machine.process.start",
    summary="Start a process on a machine",
)
async def start_process(
    request: Request,
    body: StartProcessRequest,
    resolved: UseMachine,
    processes: ProcessesDep,
    uow_factory: UowFactoryDep,
) -> ProcessResponse:
    machine = resolved.machine
    user_id = acting_user(request, resolved)
    cwd = _cwd(machine, body.cwd)
    await _refuse_when_out_of_compute(uow_factory, machine)
    base_env = await _ready_machine_for(machine, user_id, cwd)
    record = await processes.start_process(
        machine.id,
        StartProcess(
            command=body.command,
            cwd=cwd,
            env=body.env,
            tty=TerminalSize(cols=body.tty.cols, rows=body.tty.rows)
            if body.tty
            else None,
            timeout_seconds=body.timeout_seconds,
            service=body.service,
            restart_on_resume=body.restart_on_resume,
            run_id=body.run_id,
            started_by=user_id,
        ),
        base_env=base_env,
    )
    return _response(ProcessView(record, ProcessState.RUNNING, None))


@router.get(
    "/machines/{ref}/processes",
    response_model=ProcessListResponse,
    operation_id="machine.process.list",
    summary="What is running on a machine",
)
async def list_processes(
    resolved: UseMachine, processes: ProcessesDep
) -> ProcessListResponse:
    views = await processes.list(resolved.machine.id)
    return ProcessListResponse(processes=[_response(v) for v in views])


async def _view(
    processes: MachineProcesses, machine_id: UUID, process_id: str
) -> ProcessView:
    for view in await processes.list(machine_id):
        if view.record.process_id == process_id:
            return view
    record = await processes.registry.get(machine_id, process_id)
    if record is None:
        from app.modules.workspace.domain.machine_errors import MachineNotFound

        raise MachineNotFound(f"process {process_id}")
    return ProcessView(record, ProcessState.UNKNOWN, None)


@router.get(
    "/machines/{ref}/processes/{process_id}",
    response_model=ProcessResponse,
    operation_id="machine.process.get",
    summary="One process",
)
async def get_process(
    process_id: UUID, resolved: UseMachine, processes: ProcessesDep
) -> ProcessResponse:
    return _response(await _view(processes, resolved.machine.id, str(process_id)))


@router.get(
    "/machines/{ref}/processes/{process_id}/output",
    response_model=ProcessOutputResponse,
    operation_id="machine.process.output",
    summary="A page of a process's output",
)
async def process_output(
    process_id: UUID,
    resolved: UseMachine,
    processes: ProcessesDep,
    after: int = Query(
        default=0, ge=0, description="Return output after this sequence."
    ),
    wait_seconds: float = Query(
        default=0, ge=0, le=30, description="Wait this long for new output."
    ),
) -> ProcessOutputResponse:
    snapshot = await processes.output(
        resolved.machine.id,
        str(process_id),
        after_sequence=after,
        wait_seconds=wait_seconds,
    )
    return ProcessOutputResponse(
        chunks=[
            OutputChunkModel(
                sequence=chunk.sequence,
                channel=str(getattr(chunk.channel, "value", chunk.channel)),  # type: ignore[arg-type]
                data=chunk.data.decode("utf-8", errors="replace"),
            )
            for chunk in snapshot.chunks
        ],
        next_sequence=snapshot.next_sequence,
        state=str(getattr(snapshot.state, "value", snapshot.state)),
        exit_code=snapshot.exit_code,
        truncated_before_sequence=snapshot.truncated_before_sequence,
    )


@router.post(
    "/machines/{ref}/processes/{process_id}:input",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="machine.process.input",
    summary="Type into a process",
)
async def process_input(
    process_id: UUID,
    body: ProcessInputRequest,
    resolved: UseMachine,
    processes: ProcessesDep,
) -> None:
    data = (
        base64.b64decode(body.data_base64)
        if body.data_base64
        else (body.data or "").encode()
    )
    await processes.send_input(resolved.machine.id, str(process_id), data)


@router.post(
    "/machines/{ref}/processes/{process_id}:resize",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="machine.process.resize",
    summary="Resize a process's terminal",
)
async def process_resize(
    process_id: UUID,
    body: TerminalSizeModel,
    resolved: UseMachine,
    processes: ProcessesDep,
) -> None:
    await processes.resize(
        resolved.machine.id,
        str(process_id),
        TerminalSize(cols=body.cols, rows=body.rows),
    )


@router.delete(
    "/machines/{ref}/processes/{process_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="machine.process.kill",
    summary="Stop a process",
)
async def kill_process(
    process_id: UUID, resolved: UseMachine, processes: ProcessesDep
) -> None:
    await processes.kill(resolved.machine.id, str(process_id))


# --- the live socket -------------------------------------------------------------

#: How long one read waits for output before asking again. Short enough that a
#: terminal feels live, long enough that an idle one costs a request a second.
_READ_WAIT_SECONDS = 1.0


@router.websocket("/machines/{ref}/processes/{process_id}/stream")
async def stream_process(
    websocket: WebSocket,
    ref: str,
    process_id: UUID,
    processes: ProcessesDep,
    after: int = Query(default=0, ge=0),
) -> None:
    """Output out, input and resizes in, for one process.

    Out: ``{"type": "output", "sequence", "channel", "data"}`` then one
    ``{"type": "exit", "state", "exit_code"}``. In: ``{"type": "input",
    "data"}`` and ``{"type": "resize", "cols", "rows"}``; anything else is
    ignored. Authenticated like the browser view: the origin first, then the
    session from a bearer, the cookie, or ``access_token``.
    """
    from app.modules.workspace.api.controllers.machine_socket_auth import (
        authorize_socket,
    )

    resolved = await authorize_socket(websocket, ref)
    if resolved is None:
        return
    machine_id = resolved.machine.id
    await websocket.accept()
    reader = create_inherited_task(
        _pump_output(websocket, processes, machine_id, str(process_id), after),
        name="workspace.machine_process.stream_out",
    )
    try:
        while not reader.done():
            receive = create_inherited_task(
                websocket.receive_text(), name="workspace.machine_process.stream_in"
            )
            done, _ = await asyncio.wait(
                {receive, reader}, return_when=asyncio.FIRST_COMPLETED
            )
            if receive not in done:
                receive.cancel()
                with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect):
                    await receive
                break
            await _apply_message(
                processes, machine_id, str(process_id), receive.result()
            )
    except WebSocketDisconnect:
        pass
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close()


def _message(raw: str) -> bytes | TerminalSize | None:
    """One control message off the socket: what to type, or a new size.
    Anything else -- not JSON, an unknown type -- is ignored."""
    try:
        message = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(message, dict):
        return None
    if message.get("type") == "input":
        data = message.get("data")
        return data.encode() if isinstance(data, str) else None
    if message.get("type") == "resize":
        try:
            return TerminalSize(cols=int(message["cols"]), rows=int(message["rows"]))
        except KeyError, TypeError, ValueError:
            return None
    return None


async def _apply_message(
    processes: MachineProcesses, machine_id: UUID, process_id: str, raw: str
) -> None:
    message = _message(raw)
    if isinstance(message, bytes):
        await processes.send_input(machine_id, process_id, message)
    elif isinstance(message, TerminalSize):
        await processes.resize(machine_id, process_id, message)


async def _pump_output(
    websocket: WebSocket,
    processes: MachineProcesses,
    machine_id: UUID,
    process_id: str,
    after: int,
) -> None:
    sequence = after
    while True:
        snapshot = await processes.output(
            machine_id,
            process_id,
            after_sequence=sequence,
            wait_seconds=_READ_WAIT_SECONDS,
        )
        for chunk in snapshot.chunks:
            await websocket.send_json(
                {
                    "type": "output",
                    "sequence": chunk.sequence,
                    "channel": str(getattr(chunk.channel, "value", chunk.channel)),
                    "data": chunk.data.decode("utf-8", errors="replace"),
                }
            )
        sequence = snapshot.next_sequence
        if has_stopped(snapshot):
            await websocket.send_json(
                {
                    "type": "exit",
                    "state": str(getattr(snapshot.state, "value", snapshot.state)),
                    "exit_code": snapshot.exit_code,
                }
            )
            return


__all__ = ["ProcessRecord", "router"]
