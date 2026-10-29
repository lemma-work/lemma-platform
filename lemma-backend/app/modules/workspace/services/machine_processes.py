"""Processes on a machine, as the API, the Processes dock and run cleanup see them.

The sandbox runtime knows what is running and nothing else: not the command a
person typed, not which agent run started it, not that it is a service meant to
outlive that run. Those are kept here, in Redis, one hash per machine keyed by
process id, and joined with what the runtime reports. A process the runtime no
longer lists is gone and its entry is dropped on the next read.

Three kinds of process, by what ends them:

- an ordinary one ends when it exits, is killed, or reaches the lifetime cap;
- one tagged with a ``run_id`` is also killed when that run ends, so a pod
  machine shared by many runs does not fill with their leftovers;
- a **service** is exempt from both: no lifetime cap, not killed at run end,
  and restarted when its machine resumes (``restart: on-resume``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.process_output import has_stopped
from sandbox_runtime.protocol import (
    EnvironmentVariable,
    ProcessOutputSnapshot,
    ProcessState,
    TerminalSize,
)

_KEY = "machine:processes:v1:{machine_id}"
#: A service's lifetime. Not forever: a machine that is never released again
#: still gets its services re-examined once a month.
SERVICE_LIFETIME = timedelta(days=30)
#: Entries outlive their process by this much, so a finished process's exit
#: code can still be read by whoever was watching it.
_ENTRY_TTL_SECONDS = 7 * 24 * 3600


@dataclass(frozen=True, slots=True)
class ProcessRecord:
    process_id: str
    command: str
    cwd: str
    started_at: str
    started_by: str | None = None
    tty: bool = False
    run_id: str | None = None
    service: str | None = None
    restart_on_resume: bool = False
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProcessView:
    record: ProcessRecord
    state: ProcessState
    exit_code: int | None


def _encoded(record: ProcessRecord) -> str:
    """One process's record, a few hundred bytes; `_record` reads it back."""
    return json.dumps(asdict(record))


class MachineProcessRegistry:
    def __init__(self, *, redis_url: str | None = None) -> None:
        self._redis = get_redis(url=redis_url or settings.redis_url)

    def _key(self, machine_id: UUID) -> str:
        return _KEY.format(machine_id=machine_id)

    async def put(self, machine_id: UUID, record: ProcessRecord) -> None:
        key = self._key(machine_id)
        await self._redis.hset(key, record.process_id, _encoded(record))
        await self._redis.expire(key, _ENTRY_TTL_SECONDS)

    async def get(self, machine_id: UUID, process_id: str) -> ProcessRecord | None:
        raw = await self._redis.hget(self._key(machine_id), process_id)
        return _record(raw) if raw else None

    async def all(self, machine_id: UUID) -> list[ProcessRecord]:
        raw = await self._redis.hgetall(self._key(machine_id))
        records = [_record(value) for value in (raw or {}).values()]
        return [record for record in records if record is not None]

    async def drop(self, machine_id: UUID, process_ids: list[str]) -> None:
        if process_ids:
            await self._redis.hdel(self._key(machine_id), *process_ids)


def _record(raw: object) -> ProcessRecord | None:
    try:
        data = json.loads(raw if isinstance(raw, str) else bytes(raw).decode())  # type: ignore[arg-type]
        return ProcessRecord(**data)
    except TypeError, ValueError:
        return None


@dataclass(frozen=True, slots=True)
class StartProcess:
    command: str
    cwd: str
    env: dict[str, str] = field(default_factory=dict)
    tty: TerminalSize | None = None
    timeout_seconds: int | None = None
    service: str | None = None
    restart_on_resume: bool = False
    run_id: str | None = None
    started_by: UUID | None = None


class MachineProcesses:
    """Starting, reading and ending processes on one machine."""

    def __init__(self, registry: MachineProcessRegistry | None = None) -> None:
        self._registry = registry or MachineProcessRegistry()

    @property
    def registry(self) -> MachineProcessRegistry:
        return self._registry

    def _client(self):
        from app.modules.workspace.services.sandbox_composition import (
            build_local_client,
        )

        return build_local_client()

    async def start_process(
        self, machine_id: UUID, request: StartProcess, *, base_env: dict[str, str]
    ) -> ProcessRecord:
        from sandbox_runtime.protocol import WorkloadKind

        if request.service:
            await self._refuse_a_second(machine_id, request.service)
        operation_id = uuid4()
        now = datetime.now(timezone.utc)
        lifetime = (
            SERVICE_LIFETIME
            if request.service
            else timedelta(
                seconds=min(
                    request.timeout_seconds
                    or workspace_settings.process_max_lifetime_seconds,
                    workspace_settings.process_max_lifetime_seconds,
                )
            )
        )
        environment = tuple(
            EnvironmentVariable(name=name, value=value)
            for name, value in {**base_env, **request.env}.items()
        )
        await self._client().start_process(
            WorkloadKind.WORKSPACE,
            machine_id,
            operation_id=operation_id,
            deadline_at=now + lifetime,
            cwd=request.cwd,
            shell_command=request.command,
            environment=environment,
            tty=request.tty,
        )
        record = ProcessRecord(
            process_id=str(operation_id),
            command=request.command,
            cwd=request.cwd,
            started_at=now.isoformat(),
            started_by=str(request.started_by) if request.started_by else None,
            tty=request.tty is not None,
            run_id=request.run_id,
            service=request.service,
            restart_on_resume=request.restart_on_resume,
            env=dict(request.env),
        )
        await self._registry.put(machine_id, record)
        return record

    async def _refuse_a_second(self, machine_id: UUID, service: str) -> None:
        """A service is one process by its name, so a second is refused.

        Two `web` services both running left the Processes dock listing the
        same name twice, a restart-on-resume starting both, and nobody able to
        say which one a preview was pointing at. One paused under a resume
        (state unknown) is not running and does not count.
        """
        from app.modules.workspace.domain.machine_errors import MachineConflict

        for view in await self.list(machine_id):
            if (
                view.record.service == service
                and view.state is not ProcessState.UNKNOWN
                and not has_stopped(view)
            ):
                raise MachineConflict(
                    f"A service named {service!r} is already running "
                    f"(process {view.record.process_id}). Stop it first.",
                    code="MACHINE_SERVICE_RUNNING",
                )

    async def list(self, machine_id: UUID) -> list[ProcessView]:
        """What is running, joined with what Lemma knows about each process.

        A process the registry knows and the runtime no longer lists finished
        while nobody looked; it is shown once as finished and then dropped.
        """
        from sandbox_runtime.protocol import WorkloadKind

        running = {
            str(ref.operation_id): ref
            for ref in await self._client().list_processes(
                WorkloadKind.WORKSPACE, machine_id
            )
        }
        views: list[ProcessView] = []
        gone: list[str] = []
        for record in await self._registry.all(machine_id):
            ref = running.pop(record.process_id, None)
            if ref is None:
                if record.service and record.restart_on_resume:
                    # Kept: the machine paused under it, and resuming it is
                    # what `services_to_restart` is for.
                    views.append(ProcessView(record, ProcessState.UNKNOWN, None))
                else:
                    gone.append(record.process_id)
                continue
            views.append(ProcessView(record, ref.state, ref.exit_code))
        for process_id, ref in running.items():
            # Started by something that did not register it: an agent's own
            # `exec_command`. Shown, so a person can see and stop it.
            views.append(
                ProcessView(
                    ProcessRecord(
                        process_id=process_id,
                        command="",
                        cwd="",
                        started_at=(
                            ref.started_at.isoformat() if ref.started_at else ""
                        ),
                    ),
                    ref.state,
                    ref.exit_code,
                )
            )
        await self._registry.drop(machine_id, gone)
        return views

    async def output(
        self,
        machine_id: UUID,
        process_id: str,
        *,
        after_sequence: int = 0,
        wait_seconds: float = 0,
    ) -> ProcessOutputSnapshot:
        from sandbox_runtime.protocol import WorkloadKind

        return await self._client().read_process_output(
            WorkloadKind.WORKSPACE,
            machine_id,
            UUID(process_id),
            deadline_at=datetime.now(timezone.utc)
            + timedelta(seconds=wait_seconds + 30),
            after_sequence=after_sequence,
            wait_seconds=wait_seconds,
        )

    async def send_input(self, machine_id: UUID, process_id: str, data: bytes) -> None:
        from sandbox_runtime.protocol import WorkloadKind

        await self._client().send_process_input(
            WorkloadKind.WORKSPACE,
            machine_id,
            UUID(process_id),
            data,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

    async def resize(
        self, machine_id: UUID, process_id: str, size: TerminalSize
    ) -> None:
        from sandbox_runtime.protocol import WorkloadKind

        await self._client().resize_process(
            WorkloadKind.WORKSPACE,
            machine_id,
            UUID(process_id),
            size,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

    async def kill(self, machine_id: UUID, process_id: str) -> None:
        from sandbox_runtime.protocol import WorkloadKind

        await self._client().terminate_process(
            WorkloadKind.WORKSPACE,
            machine_id,
            UUID(process_id),
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

    async def end_run(self, machine_id: UUID, run_id: str) -> list[str]:
        """Kill what a finished run left running, services excepted."""
        killed: list[str] = []
        for view in await self.list(machine_id):
            record = view.record
            if record.run_id != run_id or record.service or has_stopped(view):
                continue
            await self.kill(machine_id, record.process_id)
            killed.append(record.process_id)
        return killed

    async def kill_all(self, machine_id: UUID, *, keep_services: bool) -> list[str]:
        killed: list[str] = []
        for view in await self.list(machine_id):
            if keep_services and view.record.service:
                continue
            if has_stopped(view):
                continue
            await self.kill(machine_id, view.record.process_id)
            killed.append(view.record.process_id)
        return killed

    async def services_to_restart(self, machine_id: UUID) -> list[ProcessRecord]:
        """Services whose process did not survive the machine pausing."""
        return [
            view.record
            for view in await self.list(machine_id)
            if view.record.service
            and view.record.restart_on_resume
            and view.state is ProcessState.UNKNOWN
        ]

    async def restart_services(
        self, machine_id: UUID, *, base_env: dict[str, str]
    ) -> list[ProcessRecord]:
        """Start again every service the last pause ended, under its own name."""
        restarted: list[ProcessRecord] = []
        for record in await self.services_to_restart(machine_id):
            await self._registry.drop(machine_id, [record.process_id])
            restarted.append(
                await self.start_process(
                    machine_id,
                    StartProcess(
                        command=record.command,
                        cwd=record.cwd,
                        env=record.env,
                        service=record.service,
                        restart_on_resume=True,
                        started_by=UUID(record.started_by)
                        if record.started_by
                        else None,
                    ),
                    base_env=base_env,
                )
            )
        return restarted
