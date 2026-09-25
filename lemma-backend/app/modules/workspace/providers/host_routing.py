"""One provider in front of two: the deployment's own, and the user's Mac.

A Desktop local install runs most sandboxes in its VM and a paired user's host
sandboxes on the Mac (docs/architecture/desktop-host-execution.md). The service
above was written for one provider per deployment and stays that way: this
answers every provider call by routing it on the sandbox it names.

The route is read from the sandbox id, which is where the choice is recorded
(``domain/host_execution``). So a host sandbox can only ever reach the host and
nothing else can, whatever the host is doing, and no call needs a lookup to
find out which it is. Everything that is not a host sandbox -- including every
function sandbox and every sweep -- goes to the default provider unchanged.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from datetime import datetime
from uuid import UUID

from sandbox_runtime.protocol import (
    ByteRange,
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    FileStat,
    ProcessOutputSnapshot,
    PythonResult,
    PythonSessionRef,
    StartProcessRequest,
    TerminalSize,
)

from app.modules.workspace.domain.host_execution import is_host_sandbox_id
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import (
    ProcessDescriptor,
    ProviderCreateSpec,
    ProviderInstance,
    ProviderObject,
    ProviderStorageKind,
    SandboxEndpoint,
)


class HostRoutingProvider:
    """``SandboxProvider`` + ``SandboxOpsProvider``, routed by sandbox id."""

    def __init__(self, default, host) -> None:
        self.default = default
        self.host = host
        self.name = default.name
        self.storage_kind = getattr(default, "storage_kind", ProviderStorageKind.VOLUME)
        self.resumes_stopped_instances = getattr(
            default, "resumes_stopped_instances", True
        )
        self.capabilities = getattr(default, "capabilities", frozenset())
        self.provider_name = getattr(default, "provider_name", type(default).__name__)

    # ------------------------------------------------------------ routing

    def for_sandbox(self, sandbox_id: UUID):
        return self.host if is_host_sandbox_id(sandbox_id) else self.default

    def for_name(self, name: str):
        parsed = naming.parse_container_name(name)
        return self.for_sandbox(parsed[0]) if parsed is not None else self.default

    def name_for(self, sandbox_id: UUID) -> str:
        return self.for_sandbox(sandbox_id).name

    def storage_kind_for(self, sandbox_id: UUID) -> ProviderStorageKind:
        return getattr(
            self.for_sandbox(sandbox_id), "storage_kind", ProviderStorageKind.VOLUME
        )

    # ---------------------------------------------------------- lifecycle

    async def create(self, spec: ProviderCreateSpec) -> ProviderInstance:
        return await self.for_sandbox(spec.sandbox_id).create(spec)

    async def wait_ready(
        self, instance: ProviderInstance, *, kind: SandboxKind, deadline_at: datetime
    ) -> None:
        await self.for_name(instance.name).wait_ready(
            instance, kind=kind, deadline_at=deadline_at
        )

    async def inspect(
        self, name: str, *, deadline_at: datetime
    ) -> ProviderInstance | None:
        return await self.for_name(name).inspect(name, deadline_at=deadline_at)

    async def release(
        self, instance: ProviderInstance, *, kind: SandboxKind, deadline_at: datetime
    ) -> None:
        await self.for_name(instance.name).release(
            instance, kind=kind, deadline_at=deadline_at
        )

    async def destroy(self, name: str, *, deadline_at: datetime) -> None:
        await self.for_name(name).destroy(name, deadline_at=deadline_at)

    async def find_volume(
        self, *, sandbox_id: UUID, deadline_at: datetime
    ) -> str | None:
        return await self.for_sandbox(sandbox_id).find_volume(
            sandbox_id=sandbox_id, deadline_at=deadline_at
        )

    async def ensure_volume(
        self, *, sandbox_id: UUID, name: str, deadline_at: datetime
    ) -> str | None:
        return await self.for_sandbox(sandbox_id).ensure_volume(
            sandbox_id=sandbox_id, name=name, deadline_at=deadline_at
        )

    async def destroy_volume(self, name: str, *, deadline_at: datetime) -> None:
        parsed = naming.parse_volume_name(name)
        target = self.for_sandbox(parsed[0]) if parsed is not None else self.default
        await target.destroy_volume(name, deadline_at=deadline_at)

    async def list_objects(
        self, *, deadline_at: datetime
    ) -> tuple[ProviderObject, ...]:
        """The default fabric's objects; a host has nothing to reclaim."""
        return await self.default.list_objects(deadline_at=deadline_at)

    async def close(self) -> None:
        try:
            await self.default.close()
        finally:
            await self.host.close()

    # -------------------------------------------------------------- ops

    async def start_process(
        self,
        instance: ProviderInstance,
        request: StartProcessRequest,
        *,
        deadline_at: datetime,
    ) -> str:
        return await self.for_name(instance.name).start_process(
            instance, request, deadline_at=deadline_at
        )

    async def read_process_output(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        after_sequence: int,
        wait_seconds: float,
        deadline_at: datetime,
    ) -> ProcessOutputSnapshot:
        return await self.for_name(instance.name).read_process_output(
            instance,
            process_id=process_id,
            after_sequence=after_sequence,
            wait_seconds=wait_seconds,
            deadline_at=deadline_at,
        )

    async def send_process_input(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        data: bytes,
        deadline_at: datetime,
    ) -> None:
        await self.for_name(instance.name).send_process_input(
            instance, process_id=process_id, data=data, deadline_at=deadline_at
        )

    async def resize_process(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        size: TerminalSize,
        deadline_at: datetime,
    ) -> None:
        await self.for_name(instance.name).resize_process(
            instance, process_id=process_id, size=size, deadline_at=deadline_at
        )

    async def terminate_process(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        grace_seconds: float,
        deadline_at: datetime,
    ) -> None:
        await self.for_name(instance.name).terminate_process(
            instance,
            process_id=process_id,
            grace_seconds=grace_seconds,
            deadline_at=deadline_at,
        )

    async def list_processes(
        self, instance: ProviderInstance, *, deadline_at: datetime
    ) -> tuple[ProcessDescriptor, ...]:
        return await self.for_name(instance.name).list_processes(
            instance, deadline_at=deadline_at
        )

    async def stat_file(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> FileStat:
        return await self.for_name(instance.name).stat_file(
            instance, path=path, deadline_at=deadline_at
        )

    async def list_files(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> tuple[FileStat, ...]:
        return await self.for_name(instance.name).list_files(
            instance, path=path, deadline_at=deadline_at
        )

    async def create_directory(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> None:
        await self.for_name(instance.name).create_directory(
            instance, path=path, deadline_at=deadline_at
        )

    async def open_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        byte_range: ByteRange,
        deadline_at: datetime,
    ) -> AsyncIterator[bytes]:
        stream = self.for_name(instance.name).open_file(
            instance, path=path, byte_range=byte_range, deadline_at=deadline_at
        )
        async for chunk in stream:
            yield chunk

    async def write_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        data: AsyncIterable[bytes],
        expected_sha256: str | None,
        deadline_at: datetime,
        **extra: object,
    ) -> FileStat:
        return await self.for_name(instance.name).write_file(
            instance,
            path=path,
            data=data,
            expected_sha256=expected_sha256,
            deadline_at=deadline_at,
            **extra,
        )

    async def move_file(
        self,
        instance: ProviderInstance,
        *,
        source: str,
        destination: str,
        deadline_at: datetime,
    ) -> None:
        await self.for_name(instance.name).move_file(
            instance, source=source, destination=destination, deadline_at=deadline_at
        )

    async def delete_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        recursive: bool,
        deadline_at: datetime,
    ) -> bool:
        return await self.for_name(instance.name).delete_file(
            instance, path=path, recursive=recursive, deadline_at=deadline_at
        )

    async def ensure_python_session(
        self, instance: ProviderInstance, request: CreatePythonSessionRequest
    ) -> None:
        await self.for_name(instance.name).ensure_python_session(instance, request)

    async def execute_python(
        self,
        instance: ProviderInstance,
        session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult:
        return await self.for_name(instance.name).execute_python(
            instance, session, request
        )

    async def delete_python_session(
        self, instance: ProviderInstance, *, session_id: str, deadline_at: datetime
    ) -> None:
        await self.for_name(instance.name).delete_python_session(
            instance, session_id=session_id, deadline_at=deadline_at
        )

    async def reach_port(
        self, instance: ProviderInstance, *, port: int, deadline_at: datetime
    ) -> SandboxEndpoint:
        return await self.for_name(instance.name).reach_port(
            instance, port=port, deadline_at=deadline_at
        )

    async def deliver_secret(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        value: bytes,
        deadline_at: datetime,
    ) -> None:
        await self.for_name(instance.name).deliver_secret(
            instance, path=path, value=value, deadline_at=deadline_at
        )
