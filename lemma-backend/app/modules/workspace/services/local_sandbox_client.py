"""A drop-in replacement for ``AgentBoxClient``, backed by this module.

Deliberately shaped as a client rather than a new session type. The session
above it -- its output cursor, its backpressure handling, its process
collection loop, its deterministic python-session ids -- is subtle code that
works, and rewriting it to reach a different provider would have risked all of
it to change one dependency. Matching the client surface instead means the
session is untouched and only the object behind ``self.client`` differs.

``logical_id`` is the sandbox id. That equality is not a coincidence: the
migration set each backfilled default workspace's id to the user id precisely
so every existing caller keeps addressing the same sandbox.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from agentbox.domain import (
    ByteRange,
    CreatePythonSessionRequest,
    EnvironmentVariable,
    ExecutePythonRequest,
    FileStat,
    ProcessOutputSnapshot,
    ProcessState,
    PythonResult,
    StartProcessRequest,
    TerminalSize,
)

from agentbox_client import (
    FunctionRuntimeLease,
    PortAccessGrant,
    PortProtocol,
    ProfileRef,
    WorkloadKind,
)

from app.core.config import settings
from app.modules.workspace.domain.errors import SandboxUnavailable
from app.modules.workspace.domain.sandbox import SandboxHandle
from app.modules.workspace.providers.base import ProviderGone, ProviderInstance
from app.modules.workspace.providers.profiles import FUNCTION_RUNTIME_PORT
from app.modules.workspace.services.port_access import PortAccessSigner, PortGrant
from app.modules.workspace.services.sandbox_service import SandboxService


@dataclass(frozen=True, slots=True)
class LocalProcessRef:
    """What the session actually reads off a started process.

    Deliberately not ``agentbox.domain.ProcessRef``: that type requires an
    allocation id, an allocation epoch, and a sandbox key, which are the exact
    concepts this module replaced with an epoch in the container name.
    Reusing it would have meant fabricating three fields to satisfy a shape
    nothing here believes in. The session never type-checks, it only reads.
    """

    operation_id: UUID
    state: ProcessState
    provider_process_id: str | None = None
    exit_code: int | None = None
    cwd: str | None = None
    tty: TerminalSize | None = None
    started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LocalPythonSessionRef:
    """Only the session id travels; the runtime keys sessions by it."""

    session_id: UUID


class LocalSandboxClient:
    """Speaks the AgentBox client surface over the local sandbox service."""

    def __init__(self, service: SandboxService) -> None:
        self._service = service

    # ------------------------------------------------------------------
    # Addressing
    # ------------------------------------------------------------------

    async def _instance(self, logical_id: UUID) -> tuple[SandboxHandle, ProviderInstance]:
        handle = await self._service.ensure(logical_id)
        return handle, ProviderInstance(
            provider_id=handle.provider_id, name=handle.provider_id, running=True
        )

    @property
    def _provider(self):
        return self._service._provider

    # ------------------------------------------------------------------
    # Processes
    # ------------------------------------------------------------------

    async def start_process(
        self,
        workload_kind,
        logical_id: UUID,
        *,
        operation_id: UUID,
        deadline_at: datetime,
        cwd: str,
        shell_command: str | None = None,
        argv: tuple[str, ...] | None = None,
        environment: tuple[EnvironmentVariable, ...] = (),
        tty: TerminalSize | None = None,
        output_limit_bytes: int = 1024 * 1024,
        initial_input: bytes | None = None,
    ) -> LocalProcessRef:
        del workload_kind
        handle, instance = await self._instance(logical_id)
        request = StartProcessRequest(
            operation_id=operation_id,
            shell_command=shell_command,
            argv=argv,
            cwd=cwd,
            environment=environment,
            tty=tty,
            output_limit_bytes=output_limit_bytes,
            deadline_at=deadline_at,
            initial_input=initial_input,
        )
        provider_process_id = await self._provider.start_process(
            instance, request, deadline_at=deadline_at
        )
        return LocalProcessRef(
            operation_id=operation_id,
            provider_process_id=provider_process_id,
            state=ProcessState.RUNNING,
            cwd=cwd,
            tty=tty,
        )

    async def read_process_output(
        self,
        workload_kind,
        logical_id: UUID,
        operation_id: UUID,
        *,
        deadline_at: datetime,
        after_sequence: int = 0,
        wait_seconds: float = 0,
    ) -> ProcessOutputSnapshot:
        del workload_kind
        _, instance = await self._instance(logical_id)
        return await self._provider.read_process_output(
            instance,
            process_id=self._process_id(logical_id, operation_id),
            after_sequence=after_sequence,
            wait_seconds=wait_seconds,
            deadline_at=deadline_at,
        )

    async def send_process_input(
        self,
        workload_kind,
        logical_id: UUID,
        operation_id: UUID,
        data: bytes,
        *,
        deadline_at: datetime,
    ) -> None:
        del workload_kind
        _, instance = await self._instance(logical_id)
        await self._provider.send_process_input(
            instance,
            process_id=self._process_id(logical_id, operation_id),
            data=data,
            deadline_at=deadline_at,
        )

    async def resize_process(
        self,
        workload_kind,
        logical_id: UUID,
        operation_id: UUID,
        size: TerminalSize,
        *,
        deadline_at: datetime,
    ) -> None:
        del workload_kind
        _, instance = await self._instance(logical_id)
        await self._provider.resize_process(
            instance,
            process_id=self._process_id(logical_id, operation_id),
            size=size,
            deadline_at=deadline_at,
        )

    async def terminate_process(
        self,
        workload_kind,
        logical_id: UUID,
        operation_id: UUID,
        *,
        deadline_at: datetime,
        grace_seconds: float = 5.0,
    ) -> LocalProcessRef:
        del workload_kind
        _, instance = await self._instance(logical_id)
        await self._provider.terminate_process(
            instance,
            process_id=self._process_id(logical_id, operation_id),
            grace_seconds=grace_seconds,
            deadline_at=deadline_at,
        )
        return LocalProcessRef(
            operation_id=operation_id,
            state=ProcessState.CANCELLED,
        )

    async def list_processes(
        self, workload_kind, logical_id: UUID
    ) -> tuple[LocalProcessRef, ...]:
        """Ask the sandbox what it is running.

        This deliberately holds no local process map. The backend builds a
        fresh client on every tool call, so anything remembered here would
        always be empty, and a second replica would answer differently from
        the first. The sandbox runtime is the only honest source.
        """
        del workload_kind
        _, instance = await self._instance(logical_id)
        running = await self._provider.list_processes(
            instance, deadline_at=_deadline(30)
        )
        return tuple(
            LocalProcessRef(
                operation_id=UUID(descriptor.process_id),
                provider_process_id=descriptor.process_id,
                state=descriptor.state,
                exit_code=descriptor.exit_code,
                started_at=descriptor.started_at,
            )
            for descriptor in running
        )

    @staticmethod
    def _process_id(logical_id: UUID, operation_id: UUID) -> str:
        # The runtime keys a process by the operation id it was handed, so the
        # caller's id is the handle and no mapping is needed.
        del logical_id
        return str(operation_id)

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    async def stat_file(
        self, logical_id: UUID, path: str, *, deadline_at: datetime
    ) -> FileStat:
        _, instance = await self._instance(logical_id)
        return await self._provider.stat_file(
            instance, path=path, deadline_at=deadline_at
        )

    async def list_files(
        self, logical_id: UUID, path: str, *, deadline_at: datetime
    ) -> tuple[FileStat, ...]:
        _, instance = await self._instance(logical_id)
        return await self._provider.list_files(
            instance, path=path, deadline_at=deadline_at
        )

    async def create_directory(
        self, logical_id: UUID, path: str, *, deadline_at: datetime
    ) -> None:
        _, instance = await self._instance(logical_id)
        await self._provider.create_directory(
            instance, path=path, deadline_at=deadline_at
        )

    async def read_file(
        self,
        logical_id: UUID,
        path: str,
        *,
        deadline_at: datetime,
        offset: int = 0,
        length: int | None = None,
    ) -> bytes:
        chunks: list[bytes] = []
        async with self.stream_file(
            logical_id, path, offset=offset, length=length, deadline_at=deadline_at
        ) as stream:
            async for chunk in stream:
                chunks.append(chunk)
        return b"".join(chunks)

    @asynccontextmanager
    async def stream_file(
        self,
        logical_id: UUID,
        path: str,
        *,
        deadline_at: datetime,
        offset: int = 0,
        length: int | None = None,
    ) -> AsyncIterator[AsyncIterator[bytes]]:
        _, instance = await self._instance(logical_id)
        yield self._provider.open_file(
            instance,
            path=path,
            byte_range=ByteRange(offset=offset, length=length),
            deadline_at=deadline_at,
        )

    async def write_file(
        self,
        logical_id: UUID,
        path: str,
        data: bytes,
        *,
        deadline_at: datetime,
        expected_sha256: str | None = None,
    ) -> FileStat:
        async def one_chunk() -> AsyncIterator[bytes]:
            yield data

        return await self.write_file_stream(
            logical_id,
            path,
            one_chunk(),
            expected_sha256=expected_sha256,
            deadline_at=deadline_at,
        )

    async def write_file_stream(
        self,
        logical_id: UUID,
        path: str,
        data: AsyncIterable[bytes],
        *,
        deadline_at: datetime,
        expected_sha256: str | None = None,
    ) -> FileStat:
        _, instance = await self._instance(logical_id)
        return await self._provider.write_file(
            instance,
            path=path,
            data=data,
            expected_sha256=expected_sha256,
            deadline_at=deadline_at,
        )

    async def move_file(
        self,
        logical_id: UUID,
        source: str,
        destination: str,
        *,
        deadline_at: datetime,
    ) -> None:
        _, instance = await self._instance(logical_id)
        await self._provider.move_file(
            instance, source=source, destination=destination, deadline_at=deadline_at
        )

    async def delete_file(
        self,
        logical_id: UUID,
        path: str,
        *,
        deadline_at: datetime,
        recursive: bool = False,
    ) -> None:
        _, instance = await self._instance(logical_id)
        await self._provider.delete_file(
            instance, path=path, recursive=recursive, deadline_at=deadline_at
        )

    # ------------------------------------------------------------------
    # Python sessions
    # ------------------------------------------------------------------

    async def create_python_session(
        self,
        logical_id: UUID,
        session_id: UUID,
        *,
        cwd: str,
        environment_keys: tuple[str, ...] = (),
        deadline_at: datetime,
    ) -> LocalPythonSessionRef:
        _, instance = await self._instance(logical_id)
        await self._provider.ensure_python_session(
            instance,
            CreatePythonSessionRequest(
                session_id=session_id,
                cwd=cwd,
                environment_keys=environment_keys,
                deadline_at=deadline_at,
            ),
        )
        return LocalPythonSessionRef(session_id=session_id)

    async def execute_python(
        self,
        logical_id: UUID,
        session_id: UUID,
        *,
        operation_id: UUID,
        code: str,
        environment: tuple[EnvironmentVariable, ...] = (),
        output_limit_bytes: int = 1024 * 1024,
        deadline_at: datetime,
    ) -> PythonResult:
        _, instance = await self._instance(logical_id)
        return await self._provider.execute_python(
            instance,
            LocalPythonSessionRef(session_id=session_id),
            ExecutePythonRequest(
                operation_id=operation_id,
                code=code,
                environment=environment,
                output_limit_bytes=output_limit_bytes,
                deadline_at=deadline_at,
            ),
        )

    async def delete_python_session(
        self,
        logical_id: UUID,
        session_id: UUID,
        *,
        deadline_at: datetime,
    ) -> None:
        try:
            _, instance = await self._instance(logical_id)
            await self._provider.delete_python_session(
                instance, session_id=str(session_id), deadline_at=deadline_at
            )
        except (ProviderGone, SandboxUnavailable):
            # Closing a session against a sandbox that is already gone has
            # achieved what it was asking for.
            return

    async def restart_python_session(
        self, logical_id: UUID, session_id: UUID, *, deadline_at: datetime
    ) -> LocalPythonSessionRef:
        # Deleting is enough: the next execute recreates the session lazily
        # from its deterministic id, so there is no separate restart path to
        # keep correct.
        await self.delete_python_session(
            logical_id, session_id, deadline_at=deadline_at
        )
        return LocalPythonSessionRef(session_id=session_id)

    async def inspect_process(
        self, workload_kind, logical_id: UUID, operation_id: UUID
    ) -> LocalProcessRef:
        del workload_kind
        return LocalProcessRef(
            operation_id=operation_id,
            provider_process_id=self._processes.get((logical_id, operation_id)),
            state=ProcessState.RUNNING,
        )

    # ------------------------------------------------------------------
    # Ports
    # ------------------------------------------------------------------

    async def create_port_access(
        self,
        workload_kind,
        logical_id: UUID,
        port: int,
        *,
        protocol=None,
        expires_at: datetime,
    ) -> PortAccessGrant:
        del workload_kind
        # Ensuring first is deliberate: a grant for a sandbox that is not
        # running would hand back a URL that 502s until something else
        # happens to start it.
        await self._service.ensure(logical_id)
        token = self._signer().sign(
            PortGrant(sandbox_id=logical_id, port=port, expires_at=expires_at)
        )
        base = (settings.workspace_port_access_url or settings.api_url).rstrip("/")
        return PortAccessGrant(
            workload_kind=WorkloadKind.WORKSPACE,
            logical_id=logical_id,
            port=port,
            protocol=protocol or PortProtocol.HTTP,
            url=f"{base}/workspace-ports/{token}/",
            expires_at=expires_at,
        )

    async def lease_function_runtime(
        self,
        logical_id: UUID,
        *,
        required_valid_until: datetime,
        deadline_at: datetime,
    ) -> FunctionRuntimeLease:
        """Where a pod's function runtime can be reached, right now.

        The lease is short lived and re-taken per invocation on purpose:
        AgentBox treated a lease as activity, so a long horizon kept idle
        function sandboxes alive indefinitely. Execution is the activity.
        """
        handle = await self._service.ensure(logical_id)
        _, instance = await self._instance(logical_id)
        url = await self._provider.port_base_url(
            instance, port=FUNCTION_RUNTIME_PORT, deadline_at=deadline_at
        )
        return FunctionRuntimeLease(
            logical_id=logical_id,
            allocation_id=logical_id,
            allocation_epoch=handle.epoch,
            profile=ProfileRef(
                name=settings.agentbox_function_profile_name,
                digest=settings.agentbox_function_profile_digest,
            ),
            url=url.rstrip("/") + "/",
            request_headers=(),
            expires_at=required_valid_until,
        )

    def _signer(self) -> PortAccessSigner:
        key = settings.workspace_runtime_credential_key
        if not key:
            raise RuntimeError(
                "WORKSPACE_RUNTIME_CREDENTIAL_KEY is required to sign port access"
            )
        return PortAccessSigner(key=key.encode())

    # ------------------------------------------------------------------
    # Lifecycle passthrough
    # ------------------------------------------------------------------

    async def ensure_sandbox(
        self,
        workload_kind,
        logical_id: UUID,
        *,
        profile=None,
        admission_class=None,
        deadline_at: datetime | None = None,
        verify_ready: bool = False,
    ):
        # The profile and admission class are the manager's vocabulary. Here
        # the profile lives on the sandbox row and admission is a semaphore,
        # so both are accepted and ignored rather than reshaping every caller.
        del workload_kind, profile, admission_class, deadline_at, verify_ready
        return await self._service.ensure(logical_id)

    async def inspect_sandbox(self, workload_kind, logical_id: UUID):
        del workload_kind
        return await self._service.get(logical_id)

    async def release_sandbox(
        self, workload_kind, logical_id: UUID, *, deadline_at: datetime | None = None
    ) -> None:
        del workload_kind, deadline_at
        await self._service.release(logical_id)

    async def destroy_sandbox(
        self, workload_kind, logical_id: UUID, *, deadline_at: datetime | None = None
    ) -> None:
        del workload_kind, deadline_at
        await self._service.destroy(logical_id)

    async def close(self) -> None:
        # The provider outlives any one client; disposal is the service's job.
        return None


def _deadline(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)
