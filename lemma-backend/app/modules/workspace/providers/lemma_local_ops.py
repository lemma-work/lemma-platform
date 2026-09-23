"""Operations inside a running Desktop sandbox.

The guest exposes the same workspace runtime as Docker does, so this half is
the same protocol reached over a different transport -- which is the whole
reason the Desktop provider is small: only lifecycle is genuinely different.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from datetime import datetime

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

from typing import Any

from app.modules.workspace.providers.desktop_tunnel import remember_guest_address
from app.modules.workspace.providers.base import (
    ProcessDescriptor,
    ProviderCapability,
    ProviderInstance,
    ProviderRejected,
    SandboxEndpoint,
)


class LemmaLocalOpsMixin:
    """The `SandboxOpsProvider` half of the Desktop provider."""

    capabilities = frozenset(
        {ProviderCapability.PORT_REACH, ProviderCapability.SECRET_DELIVERY}
    )

    # ------------------------------------------------------------------
    # Operations, over the same runtime protocol Docker uses
    # ------------------------------------------------------------------

    async def start_process(
        self,
        instance: ProviderInstance,
        request: StartProcessRequest,
        *,
        deadline_at: datetime,
    ) -> str:
        async with self._ops(instance, deadline_at) as client:
            started = await client.start_process(request)
            return str(started.operation_id)

    async def read_process_output(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        after_sequence: int,
        wait_seconds: float,
        deadline_at: datetime,
    ) -> ProcessOutputSnapshot:
        async with self._ops(instance, deadline_at) as client:
            return await client.read_output(
                process_id,
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
        async with self._ops(instance, deadline_at) as client:
            await client.send_input(process_id, data, deadline_at=deadline_at)

    async def resize_process(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        size: TerminalSize,
        deadline_at: datetime,
    ) -> None:
        async with self._ops(instance, deadline_at) as client:
            await client.resize(process_id, size, deadline_at=deadline_at)

    async def terminate_process(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        grace_seconds: float,
        deadline_at: datetime,
    ) -> None:
        async with self._ops(instance, deadline_at) as client:
            await client.terminate(
                process_id, grace_seconds=grace_seconds, deadline_at=deadline_at
            )

    async def list_processes(
        self, instance: ProviderInstance, *, deadline_at: datetime
    ) -> tuple[ProcessDescriptor, ...]:
        async with self._ops(instance, deadline_at) as client:
            running = await client.list_processes(deadline_at=deadline_at)
        return tuple(
            ProcessDescriptor(
                process_id=str(item.operation_id),
                state=item.state,
                exit_code=item.exit_code,
                started_at=item.started_at,
            )
            for item in running
        )

    async def stat_file(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> FileStat:
        async with self._ops(instance, deadline_at) as client:
            return await client.stat_file(path, deadline_at=deadline_at)

    async def list_files(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> tuple[FileStat, ...]:
        async with self._ops(instance, deadline_at) as client:
            return await client.list_files(path, deadline_at=deadline_at)

    async def create_directory(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> None:
        async with self._ops(instance, deadline_at) as client:
            await client.create_directory(path, deadline_at=deadline_at)

    async def open_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        byte_range: ByteRange,
        deadline_at: datetime,
    ) -> AsyncIterator[bytes]:
        async with self._ops(instance, deadline_at) as client:
            stream = await client.open_file(path, byte_range, deadline_at=deadline_at)
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
    ) -> FileStat:
        async with self._ops(instance, deadline_at) as client:
            return await client.write_file(
                path,
                data,
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
        async with self._ops(instance, deadline_at) as client:
            await client.move_file(source, destination, deadline_at=deadline_at)

    async def delete_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        recursive: bool,
        deadline_at: datetime,
    ) -> bool:
        async with self._ops(instance, deadline_at) as client:
            return await client.delete_file(
                path, recursive=recursive, deadline_at=deadline_at
            )

    async def ensure_python_session(
        self, instance: ProviderInstance, request: CreatePythonSessionRequest
    ) -> None:
        async with self._ops(instance, request.deadline_at) as client:
            await client.create_python_session(request)

    async def execute_python(
        self,
        instance: ProviderInstance,
        session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult:
        async with self._ops(instance, request.deadline_at) as client:
            return await client.execute_python(session, request)

    async def delete_python_session(
        self, instance: ProviderInstance, *, session_id: str, deadline_at: datetime
    ) -> None:
        async with self._ops(instance, deadline_at) as client:
            await client.delete_python_session(session_id, deadline_at=deadline_at)

    async def reach_port(
        self, instance: ProviderInstance, *, port: int, deadline_at: datetime
    ) -> SandboxEndpoint:
        """The guest's own address for a port it was asked to publish.

        The guest publishes only the ports declared as apps when the sandbox was
        created, so a port nobody declared is refused here rather than dialled
        and timed out. The address is loopback inside the user's own machine:
        no header opens it and nothing else can reach it.
        """
        snapshot = await self._status(instance.provider_id, deadline_at=deadline_at)
        apps = _status_object(snapshot).get("apps")
        if not isinstance(apps, dict):
            raise ProviderRejected("managed runtime omitted application endpoints")
        for value in apps.values():
            if isinstance(value, dict) and value.get("port") == port:
                url = value.get("private_url")
                if isinstance(url, str) and url:
                    return SandboxEndpoint(url=url)
        raise ProviderRejected(f"managed runtime does not expose sandbox port {port}")

    async def deliver_secret(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        value: bytes,
        deadline_at: datetime,
    ) -> None:
        """Write it through the guest runtime, which is the same protocol Docker uses."""
        _, _, name = path.rpartition("/")
        if not name:
            raise ProviderRejected(f"{path!r} does not name a file")

        async def _one_chunk() -> AsyncIterator[bytes]:
            yield value

        async with self._ops(instance, deadline_at) as client:
            # 0600, like Docker's tar entry and E2B's chmod. Delivered through
            # the ordinary file API, this took the runtime's umask and landed
            # 0644 -- so on Desktop alone the browser relay token was readable
            # by every process in the sandbox, and on no other fabric was it.
            await client.write_file(
                path,
                _one_chunk(),
                expected_sha256=None,
                deadline_at=deadline_at,
                mode=0o600,
            )


def _status_object(snapshot: dict[str, Any]) -> dict[str, Any]:
    status = snapshot.get("status")
    if not isinstance(status, dict):
        raise ProviderRejected("managed runtime status is invalid")
    # Every address the guest reports for a sandbox is one it can tunnel to,
    # and this is where every such address passes through.
    remember_guest_address(status.get("runtime_url"))
    apps = status.get("apps")
    if isinstance(apps, dict):
        for app in apps.values():
            if isinstance(app, dict):
                remember_guest_address(app.get("private_url"))
    return status
