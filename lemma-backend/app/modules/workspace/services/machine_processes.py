"""Processes on a machine, as the API, the Processes dock and run cleanup see them.

The sandbox runtime knows what is running and nothing else: not the command a
person typed, not which agent run started it, not that it is a service meant to
outlive that run. Those are kept here, in Redis, one hash per machine keyed by
process id, and joined with what the runtime reports. A process the runtime no
longer lists is gone and its entry is dropped on the next read. A service's
definition -- what it takes to start it again -- is durable, in Postgres
(`machine_services`); Redis only says which process is running it now, and
holds no environment variable of anybody's.

Three kinds of process, by what ends them:

- an ordinary one ends when it exits, is killed, or reaches the lifetime cap;
- one tagged with a ``run_id`` is also killed when that run ends, so a pod
  machine shared by many runs does not fill with their leftovers;
- a **service** is exempt from both: not killed at run end, restarted when
  its machine resumes (``restart: on-resume``) with a freshly minted token,
  and kept to its own idle ceiling rather than the process lifetime cap.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable
from uuid import UUID, uuid4

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine_errors import (
    MachineAccessDenied,
    MachineConflict,
)
from app.modules.workspace.process_output import has_stopped
from app.modules.workspace.providers.runtime_client import WorkspaceRuntimeError
from app.modules.workspace.services.machine_services import (
    MAX_SERVICES,
    MachineServices,
    ServiceDefinition,
    TokenClaims,
    mint_env,
)
from sandbox_runtime.errors import SandboxError
from sandbox_runtime.protocol import (
    EnvironmentVariable,
    ProcessOutputSnapshot,
    ProcessState,
    TerminalSize,
)

logger = get_logger(__name__)

_KEY = "machine:processes:v1:{machine_id}"
#: Held while one start of a named service checks that none is running and
#: records itself: without it two concurrent starts both found none and both
#: ran. Expires on its own, so a start that died holding it blocks the name
#: for no longer than this.
_START_CLAIM_KEY = "machine:service-start:v1:{machine_id}:{name}"
_START_CLAIM_SECONDS = 120
_RELEASE_IF_OURS = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)

#: What one service's restart can fail with without stopping the rest: the
#: machine or its runtime refusing, a mint or a decision that could not be
#: read, Redis or the database blinking.
_RESTART_FAILURES: tuple[type[BaseException], ...] = (
    SandboxError,
    WorkspaceRuntimeError,
    MachineConflict,
    MachineAccessDenied,
    RedisError,
    SQLAlchemyError,
    OSError,
    ValueError,
    LookupError,
    RuntimeError,
    TimeoutError,
)
#: A service process's hard deadline. What actually bounds a service is its
#: idle ceiling (`MACHINE_SERVICE_IDLE_SECONDS`): past it, it no longer keeps
#: its machine busy, the machine is released and the service comes back with it.
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


_RECORD_FIELDS = frozenset(f.name for f in fields(ProcessRecord))


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

    async def claim_start(self, machine_id: UUID, name: str, token: str) -> bool:
        """Whether this start of service ``name`` holds the name now; False
        while another start of it is in flight."""
        key = _START_CLAIM_KEY.format(machine_id=machine_id, name=name)
        return bool(await self._redis.set(key, token, nx=True, ex=_START_CLAIM_SECONDS))

    async def release_start(self, machine_id: UUID, name: str, token: str) -> None:
        key = _START_CLAIM_KEY.format(machine_id=machine_id, name=name)
        await self._redis.eval(_RELEASE_IF_OURS, 1, key, token)


def _record(raw: object) -> ProcessRecord | None:
    try:
        data = json.loads(raw if isinstance(raw, str) else bytes(raw).decode())  # type: ignore[arg-type]
        # Only the known fields: an entry written before `env` left the
        # registry still names it, and must still read.
        return ProcessRecord(**{k: v for k, v in data.items() if k in _RECORD_FIELDS})
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
    #: What the process's token was minted for. Kept with a service, so a
    #: restart mints the same reach afresh.
    claims: TokenClaims | None = None


class MachineProcesses:
    """Starting, reading and ending processes on one machine."""

    def __init__(
        self,
        registry: MachineProcessRegistry | None = None,
        *,
        services: MachineServices | None = None,
    ) -> None:
        self._registry = registry or MachineProcessRegistry()
        self._services_store = services

    @property
    def services(self) -> MachineServices:
        if self._services_store is None:
            self._services_store = MachineServices()
        return self._services_store

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
        if not request.service:
            return await self._start(machine_id, request, base_env=base_env)
        token = str(uuid4())
        if not await self._registry.claim_start(machine_id, request.service, token):
            raise MachineConflict(
                f"A service named {request.service!r} is being started already.",
                code="MACHINE_SERVICE_RUNNING",
            )
        try:
            await self._refuse_a_second(machine_id, request.service)
            await self._refuse_past_the_limit(machine_id, request.service)
            return await self._start(machine_id, request, base_env=base_env)
        finally:
            await self._registry.release_start(machine_id, request.service, token)

    async def _start(
        self, machine_id: UUID, request: StartProcess, *, base_env: dict[str, str]
    ) -> ProcessRecord:
        from sandbox_runtime.protocol import WorkloadKind

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
        )
        await self._registry.put(machine_id, record)
        if request.service:
            await self.services.record(
                machine_id,
                name=request.service,
                command=request.command,
                cwd=request.cwd,
                env=dict(request.env),
                claims=request.claims,
                started_by=request.started_by,
                restart_on_resume=request.restart_on_resume,
                process_id=record.process_id,
            )
        return record

    async def _refuse_a_second(self, machine_id: UUID, service: str) -> None:
        """A service is one process by its name, so a second is refused.

        Two `web` services both running left the Processes dock listing the
        same name twice, a restart-on-resume starting both, and nobody able to
        say which one a preview was pointing at. One paused under a resume
        (state unknown) is not running and does not count.
        """
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

    async def _refuse_past_the_limit(self, machine_id: UUID, service: str) -> None:
        """A new name past ``MAX_SERVICES`` is refused, not kept: a definition
        past the limit was recorded and then never brought back."""
        if await self.services.others_kept(machine_id, service) >= MAX_SERVICES:
            raise MachineConflict(
                f"This machine already keeps {MAX_SERVICES} services. Stop one "
                "you no longer need, or reuse its name.",
                code="MACHINE_SERVICE_LIMIT",
            )

    async def forget_services(self, machine_id: UUID) -> None:
        """Every service of the machine forgotten, definitions and entries:
        the machine is gone or its disk was replaced, and a service is never
        started again on a life it was not started on."""
        await self.services.forget_all(machine_id)
        await self._registry.drop(
            machine_id,
            [
                record.process_id
                for record in await self._registry.all(machine_id)
                if record.service
            ],
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

        record = await self._registry.get(machine_id, process_id)
        if record is not None and record.service:
            # Stopped on purpose: not started again after the next pause.
            await self.services.stopped(machine_id, process_id)
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

    async def _alive(self, machine_id: UUID) -> tuple[set[str], set[str]]:
        """``(running, ended_by_itself)`` among the machine's processes.

        Only a process the machine reports running counts as alive. After a
        pause E2B still lists the processes it lost, as ``unknown``, and
        reading that as alive left every service down after a resume (found
        by QA on a real E2B machine). One that exited on its own -- failed or
        done -- was not ended by the pause and is not started again: a
        service that fails at start would otherwise fail on every resume.
        """
        from sandbox_runtime.protocol import ProcessState, WorkloadKind

        running: set[str] = set()
        ended: set[str] = set()
        for ref in await self._client().list_processes(
            WorkloadKind.WORKSPACE, machine_id
        ):
            if has_stopped(ref):
                ended.add(str(ref.operation_id))
            elif str(ref.state) in (ProcessState.RUNNING, ProcessState.STARTING):
                running.add(str(ref.operation_id))
        return running, ended

    @staticmethod
    def _wants_restart(definition, running: set[str], ended: set[str]) -> bool:
        return definition.restart_on_resume and not (
            definition.process_id in running or definition.process_id in ended
        )

    async def services_to_restart(self, machine_id: UUID) -> list[ProcessRecord]:
        """Services whose process did not survive the machine pausing, from
        their durable definitions -- whatever Redis has forgotten."""
        running, ended = await self._alive(machine_id)
        return [
            ProcessRecord(
                process_id=definition.process_id or "",
                command=definition.command,
                cwd=definition.cwd,
                started_at="",
                started_by=str(definition.started_by)
                if definition.started_by
                else None,
                service=definition.name,
                restart_on_resume=True,
            )
            for definition in await self.services.running(machine_id)
            if self._wants_restart(definition, running, ended)
        ]

    async def restart_services(
        self,
        machine_id: UUID,
        *,
        may_restart: Callable[[ServiceDefinition], Awaitable[bool]],
        mint: Callable[[TokenClaims], Awaitable[dict[str, str]]] = mint_env,
    ) -> list[ProcessRecord]:
        """Start again every service the last pause ended, under its own name,
        with a token minted afresh for the claims the first start used. A
        service whose claims were never recorded starts with none.

        ``may_restart`` decides again whether its starter may use the machine
        (``machine_service_starter``): a fresh token is a fresh grant. One
        whose starter no longer may is forgotten, not started. Each service
        is its own attempt: one that fails is reported and the rest still
        start."""
        running, ended = await self._alive(machine_id)
        restarted: list[ProcessRecord] = []
        for definition in await self.services.running(machine_id):
            if not self._wants_restart(definition, running, ended):
                continue
            try:
                record = await self._restart_one(
                    machine_id, definition, may_restart, mint
                )
            except _RESTART_FAILURES as exc:
                logger.warning(
                    "workspace.machine_processes.service_restart_failed.degraded",
                    sandbox_id=str(machine_id),
                    service_name=definition.name,
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                continue
            if record is not None:
                restarted.append(record)
        return restarted

    async def _restart_one(
        self,
        machine_id: UUID,
        definition: ServiceDefinition,
        may_restart: Callable[[ServiceDefinition], Awaitable[bool]],
        mint: Callable[[TokenClaims], Awaitable[dict[str, str]]],
    ) -> ProcessRecord | None:
        if definition.process_id:
            await self._registry.drop(machine_id, [definition.process_id])
        if not await may_restart(definition):
            await self.services.forget(machine_id, definition.name)
            logger.info(
                "workspace.machine_processes.service_forgotten_starter_lost_access.observed",
                sandbox_id=str(machine_id),
                service_name=definition.name,
            )
            return None
        base_env = await mint(definition.claims) if definition.claims else {}
        return await self.start_process(
            machine_id,
            StartProcess(
                command=definition.command,
                cwd=definition.cwd,
                env=definition.revealed_env(),
                service=definition.name,
                restart_on_resume=True,
                started_by=definition.started_by,
                claims=definition.claims,
            ),
            base_env=base_env,
        )
