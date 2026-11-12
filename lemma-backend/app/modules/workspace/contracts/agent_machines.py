"""Where an agent run's commands run, for the agent module's tools.

The agent module names a machine (the agent's `machine:`, or a tool call's
`machine` argument) and this decides it with the same resolver and matrix as
every `/machines` route: a person's default machine only for a run they
started in Lemma; a pod machine by visibility or grant; a connected machine by
share and grant.

A pod machine is shared, so work on one also gets what sharing needs: a slot
(``run_slots`` at a time), its own working directory under
``~/lemma/runs/<agent>/<conversation>``, the disk janitor before it starts,
and -- when it is over -- its leftover processes stopped and its slot freed.

"A run" here is a conversation's work, not one agent run: a conversation
that waits on its own build ends a run while the build goes on, and the run
that picks it up must find the same directory, slot and processes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from app.modules.workspace.domain.machine import Machine, MachineKind
from app.modules.workspace.domain.machine_access import (
    CallerKind,
    MachineAccess,
    MachineCaller,
)
from app.modules.workspace.domain.machine_errors import MachineNotFound
from sandbox_runtime.paths import RUNS_ROOT

_SLUG = re.compile(r"[^a-z0-9-]+")


@dataclass(frozen=True, slots=True)
class AgentMachine:
    machine: Machine
    #: The working directory this run uses there: its own on a shared
    #: machine, None (the conversation's) on a person's own.
    run_workdir: str | None

    @property
    def machine_id(self) -> UUID:
        return self.machine.id

    @property
    def shared(self) -> bool:
        return self.machine.kind is not MachineKind.DEFAULT

    @property
    def ref(self) -> str:
        return self.machine.ref


class ChannelRunHasNoMachine(MachineNotFound):
    """A run started from a channel asked for a person's own machine."""

    def __init__(self) -> None:
        super().__init__("me")
        self.message = (
            "This run was not started by its person in Lemma, so it cannot use "
            "anyone's own machine. Give the agent a pod machine "
            "(machine: pod:<name>) to run commands here."
        )


def _slug(value: str | None) -> str:
    return _SLUG.sub("-", (value or "agent").lower()).strip("-")[:40] or "agent"


def run_workdir(agent_name: str | None, run_id: UUID) -> str:
    return f"{RUNS_ROOT}/{_slug(agent_name)}/{run_id}"


async def resolve_agent_machine(
    *,
    ref: str | None,
    user_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None,
    agent_name: str | None,
    conversation_id: UUID | None,
    run_id: UUID | None,
) -> AgentMachine:
    """The machine for this run's next command, refused with a sentence if the
    run may not use it."""
    from app.core.api.dependencies import get_uow_factory
    from app.modules.workspace.services.machine_resolver import MachineResolver

    caller = MachineCaller(
        CallerKind.AGENT,
        user_id=user_id,
        pod_id=pod_id,
        workload_id=agent_id or pod_id,
        conversation_id=conversation_id,
    )
    target = (ref or "me").strip() or "me"
    async with get_uow_factory()() as uow:
        try:
            resolved = await MachineResolver(uow).resolve(
                target, caller, need=MachineAccess.USE
            )
        except MachineNotFound:
            if target == "me":
                raise ChannelRunHasNoMachine() from None
            raise
        await uow.commit()
    machine = resolved.machine
    if machine.kind is MachineKind.DEFAULT or run_id is None:
        return AgentMachine(machine=machine, run_workdir=None)
    return AgentMachine(machine=machine, run_workdir=run_workdir(agent_name, run_id))


async def begin_run(machine: AgentMachine, *, run_id: UUID, acting_user: UUID) -> None:
    """Take a slot and make room, once per run per shared machine; renew the
    slot on every later command."""
    if not machine.shared:
        return
    from app.modules.workspace.services.disk_janitor import keep_room
    from app.modules.workspace.services.run_slots import RunSlots

    slots = RunSlots()
    already = str(run_id) in await slots.holders(machine.machine_id)
    if not already:
        await keep_room(machine.machine_id, acting_user=acting_user)
    await slots.acquire(machine.machine_id, run_id, slots=machine.machine.run_slots)


async def tag_run_process(
    machine: AgentMachine,
    *,
    run_id: UUID,
    process_id: str,
    command: str,
    cwd: str,
    started_by: UUID,
) -> None:
    """Remember that a run left a process running, so its end stops it."""
    if not machine.shared:
        return
    from datetime import datetime, timezone

    from app.modules.workspace.services.machine_processes import (
        MachineProcessRegistry,
        ProcessRecord,
    )

    await MachineProcessRegistry().put(
        machine.machine_id,
        ProcessRecord(
            process_id=process_id,
            command=command,
            cwd=cwd,
            started_at=datetime.now(timezone.utc).isoformat(),
            started_by=str(started_by),
            run_id=str(run_id),
        ),
    )


async def end_run(run_id: UUID) -> list[str]:
    """Stop what the run left on shared machines and free its slots."""
    from app.modules.workspace.services.machine_processes import MachineProcesses
    from app.modules.workspace.services.run_slots import RunSlots

    slots = RunSlots()
    processes = MachineProcesses()
    stopped: list[str] = []
    for machine_id in await slots.machines_of(run_id):
        stopped.extend(await processes.end_run(machine_id, str(run_id)))
        await slots.release(machine_id, run_id)
    await slots.forget_run(run_id)
    return stopped


async def machines_for_agent(
    *,
    user_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None,
    conversation_id: UUID | None,
    usual: str | None,
) -> list[dict[str, object]]:
    """Every machine this run may use, as the `list_machines` tool reports it."""
    from app.core.api.dependencies import get_uow_factory
    from app.modules.workspace.services.machine_resolver import MachineResolver

    caller = MachineCaller(
        CallerKind.AGENT,
        user_id=user_id,
        pod_id=pod_id,
        workload_id=agent_id or pod_id,
        conversation_id=conversation_id,
    )
    async with get_uow_factory()() as uow:
        visible = await MachineResolver(uow).visible(caller, pod_ids=[pod_id])
        await uow.commit()
    usual_ref = (usual or "me").strip()
    summaries: list[dict[str, object]] = []
    for resolved in visible:
        if resolved.access < MachineAccess.USE:
            continue
        machine = resolved.machine
        ref = (
            machine.ref
            if machine.kind is not MachineKind.POD
            else f"pod:{machine.name}"
        )
        summaries.append(
            {
                "ref": ref,
                "name": machine.display_name,
                "kind": machine.kind.value,
                "size": machine.size.label,
                "template": machine.template.value,
                "status": machine.status.value,
                "docker": machine.capabilities.docker,
                "yours": ref == usual_ref or str(machine.id) == usual_ref,
            }
        )
    return summaries


async def expose_port(
    machine: AgentMachine,
    *,
    port: int,
    visibility: str,
    ttl_seconds: int | None,
    created_by: UUID,
) -> tuple[str, str | None]:
    from app.core.api.dependencies import get_uow_factory
    from app.modules.workspace.services.machine_previews import MachinePreviews

    preview = await MachinePreviews(get_uow_factory()).create(
        machine.machine_id,
        port=port,
        visibility=visibility,
        ttl_seconds=ttl_seconds,
        created_by=created_by,
    )
    return preview.url, preview.expires_at.isoformat() if preview.expires_at else None


async def sync_files(
    machine: AgentMachine,
    *,
    direction: str,
    machine_path: str,
    pod_id: UUID,
    pod_path: str,
    user_id: UUID,
    cwd: str,
) -> tuple[list[str], int]:
    import posixpath

    from app.core.api.dependencies import get_uow_factory
    from app.modules.workspace.domain.machine_errors import MachineConflict
    from app.modules.workspace.services.machine_file_sync import (
        sync_from_pod,
        sync_to_pod,
    )
    from sandbox_runtime.paths import is_inside_home, is_protected

    absolute = posixpath.normpath(
        machine_path
        if machine_path.startswith("/")
        else posixpath.join(cwd, machine_path)
    )
    if not is_inside_home(absolute):
        raise MachineConflict(
            "Sync files inside the machine's home.", code="MACHINE_PATH_OUTSIDE_HOME"
        )
    if direction == "from_pod":
        if is_protected(absolute):
            raise MachineConflict(
                "That would overwrite the browser's profile, which holds your logins.",
                code="MACHINE_PATH_PROTECTED",
            )
        return await sync_from_pod(
            get_uow_factory(),
            machine_id=machine.machine_id,
            user_id=user_id,
            pod_id=pod_id,
            pod_path=pod_path,
            machine_path=absolute,
        )
    return await sync_to_pod(
        get_uow_factory(),
        machine_id=machine.machine_id,
        user_id=user_id,
        pod_id=pod_id,
        pod_folder=pod_path,
        machine_path=absolute,
        max_files=500,
        max_bytes=256 * 1024 * 1024,
    )


async def reset_for_agent(
    machine: AgentMachine, *, scopes: list[str], acting_user: UUID
) -> tuple[int, list[str]]:
    """The scopes an agent may clean: nothing that costs a person a login or
    a disk."""
    from app.core.api.dependencies import get_uow_factory
    from app.modules.workspace.domain.machine_errors import MachineAccessDenied
    from app.modules.workspace.services.machine_lifecycle import (
        AGENT_SCOPES,
        MachineLifecycle,
    )

    if not set(scopes) <= AGENT_SCOPES:
        raise MachineAccessDenied(machine.ref, "reset that scope of")
    outcomes = await MachineLifecycle(get_uow_factory()).reset(
        machine.machine, scopes, acting_user=acting_user
    )
    freed = sum(outcome.freed_bytes or 0 for outcome in outcomes)
    details = [
        f"{outcome.scope}: {outcome.detail}" if outcome.detail else outcome.scope
        for outcome in outcomes
    ]
    return freed, details


@dataclass(frozen=True, slots=True)
class MachineBrief:
    """What the run context says about the machine, in a few lines."""

    ref: str
    size: str
    template: str
    docker: bool
    workdir: str | None
    disk_fraction: float | None
    compute_left: float | None
    compute_resets: str | None


async def machine_brief(
    *,
    ref: str | None,
    user_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None,
    agent_name: str | None,
    conversation_id: UUID | None,
    run_id: UUID | None,
) -> MachineBrief | None:
    """None when the run has no machine it may use. Reads rows and Redis only:
    building a run's context must never wake a machine."""
    from app.core.api.dependencies import get_uow_factory
    from app.modules.workspace.domain.sandbox import SandboxOwnerKind
    from app.modules.workspace.services.compute_meter import verdict_for
    from app.modules.workspace.services.disk_janitor import last_disk_fraction

    try:
        target = await resolve_agent_machine(
            ref=ref,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=agent_id,
            agent_name=agent_name,
            conversation_id=conversation_id,
            run_id=run_id,
        )
    except MachineNotFound:
        return None
    machine = target.machine
    compute_left = None
    resets = None
    if machine.kind is not MachineKind.CONNECTED:
        async with get_uow_factory()() as uow:
            verdict = await verdict_for(
                uow,
                owner_kind=SandboxOwnerKind.POD
                if machine.kind is MachineKind.POD
                else SandboxOwnerKind.USER,
                owner_id=machine.owner_id,
                created_by=machine.created_by,
            )
        if verdict.fraction is not None:
            compute_left = max(0.0, 1.0 - verdict.fraction)
            resets = f"{verdict.resets_at:%d %B}"
    return MachineBrief(
        ref=machine.ref
        if machine.kind is not MachineKind.POD
        else f"pod:{machine.name}",
        size=machine.size.label,
        template=machine.template.value,
        docker=machine.capabilities.docker,
        workdir=target.run_workdir,
        disk_fraction=await last_disk_fraction(machine.id),
        compute_left=compute_left,
        compute_resets=resets,
    )


__all__ = [
    "MachineBrief",
    "expose_port",
    "machine_brief",
    "machines_for_agent",
    "reset_for_agent",
    "sync_files",
    "AgentMachine",
    "ChannelRunHasNoMachine",
    "begin_run",
    "end_run",
    "resolve_agent_machine",
    "run_workdir",
    "tag_run_process",
]
