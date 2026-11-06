"""Every connected machine behind one provider, the way the sandbox service wants it.

The service talks to one provider per deployment; ``HostRoutingProvider`` sends
it a call for a connected machine's sandbox (its id carries the connected tag,
``domain/machine_connection.is_connected_machine_id``) and this answers it. A
connected machine's connection and its sandbox share one id, so each call names
its connection; this reads the connection's row -- its name, home and reported
ports -- and hands the call to a ``ConnectedMachineProvider`` for it.

The row is remembered for a few seconds, in this process, the way
``lemma_local`` remembers a guest's runtime address: a file operation makes
several calls in a row, and what is remembered is an address and a name, worth
nothing to another process. A machine whose connection is gone or revoked is
``MachineNotConnected``, as one whose link is down is.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
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

from app.core.bounded import BoundedDict
from app.modules.workspace.domain.machine_errors import MachineNotConnected
from app.modules.workspace.domain.machine_connection import (
    MachineConnection,
)
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import (
    ProcessDescriptor,
    ProviderCapability,
    ProviderCreateSpec,
    ProviderInstance,
    ProviderObject,
    ProviderStorageKind,
    SandboxEndpoint,
)
from app.modules.workspace.providers.connected import ConnectedMachineProvider
from app.modules.workspace.providers.docker import RuntimeCredentialSigner
from app.modules.workspace.providers.runtime_client import RuntimeState

#: How long a connection's row is believed without reading it again.
_ROW_TTL_SECONDS = 5.0

ConnectionLookup = Callable[[UUID], Awaitable[MachineConnection | None]]


class ConnectedFabricProvider:
    name = "connected"
    provider_name = "connected"
    storage_kind = ProviderStorageKind.SANDBOX_NATIVE
    resumes_stopped_instances = True
    capabilities = frozenset(
        {ProviderCapability.PORT_REACH, ProviderCapability.SECRET_DELIVERY}
    )

    def __init__(
        self, lookup: ConnectionLookup, runtime_credentials: RuntimeCredentialSigner
    ) -> None:
        self._lookup = lookup
        self._runtime_credentials = runtime_credentials
        self._rows: BoundedDict[UUID, tuple[float, MachineConnection]] = BoundedDict(
            maxsize=1024
        )

    async def for_machine(self, machine_id: UUID) -> ConnectedMachineProvider:
        cached = self._rows.get(machine_id)
        if cached is not None and cached[0] > time.monotonic():
            connection = cached[1]
        else:
            found = await self._lookup(machine_id)
            if found is None or found.revoked_at is not None:
                self._rows.pop(machine_id, None)
                raise MachineNotConnected(found.name if found else str(machine_id))
            connection = found
            self._rows[machine_id] = (time.monotonic() + _ROW_TTL_SECONDS, connection)
        return ConnectedMachineProvider(connection, self._runtime_credentials)

    async def _named(self, name: str) -> ConnectedMachineProvider:
        parsed = naming.parse_container_name(name)
        if parsed is None:
            raise MachineNotConnected(name)
        return await self.for_machine(parsed[0])

    async def _of(self, instance: ProviderInstance) -> ConnectedMachineProvider:
        return await self._named(instance.name)

    # ---------------------------------------------------------- lifecycle

    async def create(self, spec: ProviderCreateSpec) -> ProviderInstance:
        return await (await self.for_machine(spec.sandbox_id)).create(spec)

    async def wait_ready(
        self, instance: ProviderInstance, *, kind: SandboxKind, deadline_at: datetime
    ) -> None:
        await (await self._of(instance)).wait_ready(
            instance, kind=kind, deadline_at=deadline_at
        )

    async def inspect(
        self, name: str, *, deadline_at: datetime
    ) -> ProviderInstance | None:
        return await (await self._named(name)).inspect(name, deadline_at=deadline_at)

    async def release(
        self, instance: ProviderInstance, *, kind: SandboxKind, deadline_at: datetime
    ) -> None:
        """Nothing to stop: the machine is the person's and stays on."""

    async def destroy(self, name: str, *, deadline_at: datetime) -> None:
        """Never: forgetting a machine is revoking its connection."""

    async def find_volume(
        self, *, sandbox_id: UUID, deadline_at: datetime
    ) -> str | None:
        return None

    async def ensure_volume(
        self, *, sandbox_id: UUID, name: str, deadline_at: datetime
    ) -> str | None:
        return None

    async def destroy_volume(self, name: str, *, deadline_at: datetime) -> None:
        return None

    async def list_objects(
        self, *, deadline_at: datetime
    ) -> tuple[ProviderObject, ...]:
        return ()

    async def close(self) -> None:
        self._rows.clear()

    # ---------------------------------------------------------------- ops

    async def start_process(
        self,
        instance: ProviderInstance,
        request: StartProcessRequest,
        *,
        deadline_at: datetime,
    ) -> str:
        return await (await self._of(instance)).start_process(
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
        return await (await self._of(instance)).read_process_output(
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
        await (await self._of(instance)).send_process_input(
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
        await (await self._of(instance)).resize_process(
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
        await (await self._of(instance)).terminate_process(
            instance,
            process_id=process_id,
            grace_seconds=grace_seconds,
            deadline_at=deadline_at,
        )

    async def runtime_state(
        self, instance: ProviderInstance, *, deadline_at: datetime
    ) -> RuntimeState:
        return await (await self._of(instance)).runtime_state(
            instance, deadline_at=deadline_at
        )

    async def list_processes(
        self, instance: ProviderInstance, *, deadline_at: datetime
    ) -> tuple[ProcessDescriptor, ...]:
        return await (await self._of(instance)).list_processes(
            instance, deadline_at=deadline_at
        )

    async def stat_file(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> FileStat:
        return await (await self._of(instance)).stat_file(
            instance, path=path, deadline_at=deadline_at
        )

    async def list_files(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> tuple[FileStat, ...]:
        return await (await self._of(instance)).list_files(
            instance, path=path, deadline_at=deadline_at
        )

    async def create_directory(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> None:
        await (await self._of(instance)).create_directory(
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
        provider = await self._of(instance)
        async for chunk in provider.open_file(
            instance, path=path, byte_range=byte_range, deadline_at=deadline_at
        ):
            yield chunk

    async def write_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        data: AsyncIterable[bytes],
        expected_sha256: str | None,
        deadline_at: datetime,
    ) -> FileStat:
        return await (await self._of(instance)).write_file(
            instance,
            path=path,
            data=data,
            expected_sha256=expected_sha256,
            deadline_at=deadline_at,
        )

    async def move_file(
        self,
        instance: ProviderInstance,
        *,
        source: str,
        destination: str,
        deadline_at: datetime,
    ) -> None:
        await (await self._of(instance)).move_file(
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
        return await (await self._of(instance)).delete_file(
            instance, path=path, recursive=recursive, deadline_at=deadline_at
        )

    async def ensure_python_session(
        self, instance: ProviderInstance, request: CreatePythonSessionRequest
    ) -> None:
        await (await self._of(instance)).ensure_python_session(instance, request)

    async def execute_python(
        self,
        instance: ProviderInstance,
        session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult:
        return await (await self._of(instance)).execute_python(
            instance, session, request
        )

    async def delete_python_session(
        self, instance: ProviderInstance, *, session_id: str, deadline_at: datetime
    ) -> None:
        await (await self._of(instance)).delete_python_session(
            instance, session_id=session_id, deadline_at=deadline_at
        )

    async def reach_port(
        self, instance: ProviderInstance, *, port: int, deadline_at: datetime
    ) -> SandboxEndpoint:
        return await (await self._of(instance)).reach_port(
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
        await (await self._of(instance)).deliver_secret(
            instance, path=path, value=value, deadline_at=deadline_at
        )
