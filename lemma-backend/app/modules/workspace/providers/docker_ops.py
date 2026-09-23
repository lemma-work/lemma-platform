"""Operations inside a running Docker sandbox.

Everything here speaks the workspace runtime's HTTP protocol rather than the
Docker Engine API: once a container exists, what happens inside it is the
runtime's business, and Docker's only remaining job is to say where to reach it.
That is why this half is identical in shape to the Desktop provider's, which
talks to the same runtime over a different transport.
"""

from __future__ import annotations

import asyncio
import tarfile
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO

import httpx

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

from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers.base import (
    LABEL_SANDBOX_KIND,
    ProcessDescriptor,
    ProviderCapability,
    ProviderFailed,
    ProviderGone,
    ProviderInstance,
    ProviderNotReady,
    ProviderRejected,
    SandboxEndpoint,
)
from app.modules.workspace.providers.docker_engine import (
    DockerContainerInspect,
    DockerEngineError,
)
from app.modules.workspace.providers.profiles import SandboxProfile, profile_for
from app.modules.workspace.providers.runtime_client import (
    WorkspaceRuntimeClient,
)
from app.modules.workspace.providers.runtime_errors import (
    WorkspaceRuntimeError,
    WorkspaceRuntimeFileConflict,
    WorkspaceRuntimeFileNotFound,
    WorkspaceRuntimeFileRejected,
    WorkspaceRuntimeProcessGone,
    WorkspaceRuntimeUnauthorized,
)

# The runtime reads this path once on start and unlinks it, so delivering a
# credential means writing here rather than setting an environment variable
# that would outlive the container in `docker inspect`.
_BOOTSTRAP_DIR = "/run/lemma-bootstrap"


class DockerOpsMixin:
    """The `SandboxOpsProvider` half of the Docker provider."""

    capabilities = frozenset(
        {ProviderCapability.PORT_REACH, ProviderCapability.SECRET_DELIVERY}
    )

    # ------------------------------------------------------------------
    # Operations inside the sandbox
    # ------------------------------------------------------------------

    async def start_process(
        self,
        instance: ProviderInstance,
        request: StartProcessRequest,
        *,
        deadline_at: datetime,
    ) -> str:
        async with self._ops_client(instance, deadline_at=deadline_at) as client:
            started = await client.start_process(request)
            # The runtime keys a process by the operation id it was handed, so
            # that id is the handle. There is no separate provider id to track.
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
        async with self._ops_client(instance, deadline_at=deadline_at) as client:
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
        async with self._ops_client(instance, deadline_at=deadline_at) as client:
            await client.send_input(process_id, data, deadline_at=deadline_at)

    async def resize_process(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        size: TerminalSize,
        deadline_at: datetime,
    ) -> None:
        async with self._ops_client(instance, deadline_at=deadline_at) as client:
            await client.resize(process_id, size, deadline_at=deadline_at)

    async def terminate_process(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        grace_seconds: float,
        deadline_at: datetime,
    ) -> None:
        async with self._ops_client(instance, deadline_at=deadline_at) as client:
            await client.terminate(
                process_id, grace_seconds=grace_seconds, deadline_at=deadline_at
            )

    async def list_processes(
        self, instance: ProviderInstance, *, deadline_at: datetime
    ) -> tuple[ProcessDescriptor, ...]:
        async with self._ops_client(instance, deadline_at=deadline_at) as client:
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
        async with self._ops_client(instance, deadline_at=deadline_at) as client:
            return await client.stat_file(path, deadline_at=deadline_at)

    async def list_files(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> tuple[FileStat, ...]:
        async with self._ops_client(instance, deadline_at=deadline_at) as client:
            return await client.list_files(path, deadline_at=deadline_at)

    async def create_directory(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> None:
        async with self._ops_client(instance, deadline_at=deadline_at) as client:
            await client.create_directory(path, deadline_at=deadline_at)

    async def open_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        byte_range: ByteRange,
        deadline_at: datetime,
    ) -> AsyncIterator[bytes]:
        async with self._ops_client(instance, deadline_at=deadline_at) as client:
            # open_file returns the iterator; it is not itself a generator.
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
        async with self._ops_client(instance, deadline_at=deadline_at) as client:
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
        async with self._ops_client(instance, deadline_at=deadline_at) as client:
            await client.move_file(source, destination, deadline_at=deadline_at)

    async def delete_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        recursive: bool,
        deadline_at: datetime,
    ) -> bool:
        async with self._ops_client(instance, deadline_at=deadline_at) as client:
            return await client.delete_file(
                path, recursive=recursive, deadline_at=deadline_at
            )

    async def ensure_python_session(
        self,
        instance: ProviderInstance,
        request: CreatePythonSessionRequest,
    ) -> None:
        async with self._ops_client(
            instance, deadline_at=request.deadline_at
        ) as client:
            await client.create_python_session(request)

    async def execute_python(
        self,
        instance: ProviderInstance,
        session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult:
        async with self._ops_client(
            instance, deadline_at=request.deadline_at
        ) as client:
            return await client.execute_python(session, request)

    async def delete_python_session(
        self, instance: ProviderInstance, *, session_id: str, deadline_at: datetime
    ) -> None:
        async with self._ops_client(instance, deadline_at=deadline_at) as client:
            await client.delete_python_session(session_id, deadline_at=deadline_at)

    # ------------------------------------------------------------------
    # Runtime plumbing
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _ops_client(
        self, instance: ProviderInstance, *, deadline_at: datetime
    ) -> AsyncIterator[WorkspaceRuntimeClient]:
        from sandbox_runtime.errors import (
            SandboxPathConflict,
            SandboxPathNotFound,
            SandboxProcessNotFound,
            SandboxRejected,
            SandboxUnauthorized,
            SandboxUnavailable,
        )

        client: WorkspaceRuntimeClient | None = None
        try:
            client = await self._runtime_client(
                instance.provider_id, deadline_at=deadline_at
            )
            yield client
        except WorkspaceRuntimeFileNotFound as exc:
            raise SandboxPathNotFound(str(exc)) from exc
        except WorkspaceRuntimeFileConflict as exc:
            raise SandboxPathConflict(str(exc)) from exc
        except WorkspaceRuntimeFileRejected as exc:
            raise SandboxRejected(str(exc)) from exc
        except WorkspaceRuntimeProcessGone as exc:
            # Definitive, and about the process rather than the sandbox.
            # `ProviderGone` would make the client forget its handle to a
            # workspace that is fine; `SandboxUnavailable` would retry a
            # process that will never exist until the deadline.
            raise SandboxProcessNotFound(str(exc)) from exc
        except WorkspaceRuntimeUnauthorized as exc:
            # Definitive: this credential will not become valid by waiting.
            raise SandboxUnauthorized(str(exc)) from exc
        except ProviderGone:
            raise
        except asyncio.TimeoutError as exc:
            raise SandboxUnavailable(
                "workspace runtime did not answer before the deadline"
            ) from exc
        except (WorkspaceRuntimeError, DockerEngineError) as exc:
            raise SandboxUnavailable(str(exc)) from exc
        finally:
            if client is not None:
                await client.close()

    async def _runtime_client(
        self, provider_id: str, *, deadline_at: datetime
    ) -> WorkspaceRuntimeClient:
        inspected = await self._engine.inspect_container(
            provider_id, deadline_at=deadline_at
        )
        if inspected is None:
            # A stale epoch names a container that no longer exists. This is
            # the fence firing, and it is definitive rather than retryable.
            raise ProviderGone(f"sandbox container {provider_id} no longer exists")
        if self._runtime_credentials is None:
            raise WorkspaceRuntimeError("runtime credentials are not configured")
        kind = SandboxKind(
            inspected.config.labels.get(LABEL_SANDBOX_KIND, SandboxKind.WORKSPACE.value)
        )
        return self._client_from_inspect(
            inspected,
            runtime_port=profile_for(kind).runtime_port,
            token=self._runtime_credentials.token(provider_id),
        )

    async def _wait_workspace_runtime(
        self,
        inspected: DockerContainerInspect,
        *,
        profile: SandboxProfile,
        deadline_at: datetime,
    ) -> None:
        if self._runtime_credentials is None:
            raise ProviderFailed(
                "Docker workspace runtime credentials are not configured"
            )
        token = self._runtime_credentials.token(inspected.container_id)
        client = self._client_from_inspect(
            inspected,
            runtime_port=profile.runtime_port,
            token=token,
            request_timeout_seconds=0.25,
        )
        try:
            try:
                await client.health(deadline_at=deadline_at)
                return
            except WorkspaceRuntimeError:
                # Not answering yet is the expected case on a cold or
                # resumed container; the credential delivery below is what
                # this call exists to do.
                pass
            # The runtime reads this file once and unlinks it, so a resumed
            # container needs it delivered again before it will answer.
            await self._engine.put_archive(
                inspected.container_id,
                _BOOTSTRAP_DIR,
                _token_archive(token),
                deadline_at=deadline_at,
            )
            while datetime.now(timezone.utc) < deadline_at:
                try:
                    await client.health(deadline_at=deadline_at)
                    return
                except WorkspaceRuntimeError:
                    await asyncio.sleep(0.05)
            raise ProviderNotReady("Docker workspace runtime is still starting")
        finally:
            await client.close()

    async def _wait_function_runtime(
        self,
        inspected: DockerContainerInspect,
        *,
        profile: SandboxProfile,
        deadline_at: datetime,
    ) -> None:
        base_url = self._base_url(inspected, runtime_port=profile.runtime_port)
        async with httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(0.25), follow_redirects=False
        ) as client:
            while datetime.now(timezone.utc) < deadline_at:
                try:
                    if (await client.get("/healthz")).status_code == 200:
                        return
                except httpx.TransportError:
                    # Still starting: nothing is listening yet. Poll until
                    # the deadline rather than failing on the first refusal.
                    pass
                await asyncio.sleep(0.05)
        raise ProviderNotReady("Docker function runtime is still starting")

    def _client_from_inspect(
        self,
        inspected: DockerContainerInspect,
        *,
        runtime_port: int,
        token: str,
        request_timeout_seconds: float = 35,
    ) -> WorkspaceRuntimeClient:
        return WorkspaceRuntimeClient(
            self._base_url(inspected, runtime_port=runtime_port),
            token,
            request_timeout_seconds=request_timeout_seconds,
        )

    def _base_url(self, inspected: DockerContainerInspect, *, runtime_port: int) -> str:
        if self._config.private_network:
            attachment = inspected.network_settings.networks.get(
                self._config.private_network
            )
            if attachment is None or not attachment.ip_address:
                raise WorkspaceRuntimeError(
                    "Docker runtime is not attached to the configured private network"
                )
            return f"http://{attachment.ip_address}:{runtime_port}"
        bindings = inspected.network_settings.ports.get(f"{runtime_port}/tcp")
        if not bindings:
            raise WorkspaceRuntimeError("Docker runtime port is not published")
        return f"http://127.0.0.1:{bindings[0].host_port}"

    async def reach_port(
        self, instance: ProviderInstance, *, port: int, deadline_at: datetime
    ) -> SandboxEndpoint:
        """Where a published port of this sandbox can be reached.

        No headers and not public: the port is either on a private Docker
        network or bound to loopback on the host, so reaching it at all already
        means being inside the trust boundary.
        """
        inspected = await self._engine.inspect_container(
            instance.provider_id, deadline_at=deadline_at
        )
        if inspected is None:
            raise ProviderGone(f"sandbox container {instance.provider_id} is gone")
        try:
            url = self._base_url(inspected, runtime_port=port)
        except WorkspaceRuntimeError as exc:
            # `reach_port` sits outside `_ops_client`, so nothing mapped this.
            # A port the container never published raised a raw
            # `WorkspaceRuntimeError` straight through the provider: the port
            # proxy catches `ProviderGone`/`ProviderRejected` and turns them
            # into a 404, and caught neither of these -- so Docker answered an
            # undeclared port with an unhandled 500 and an unframed WebSocket
            # close, where Desktop answers a clean 404.
            raise ProviderRejected(str(exc)) from exc
        return SandboxEndpoint(url=url)

    async def deliver_secret(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        value: bytes,
        deadline_at: datetime,
    ) -> None:
        directory, _, name = path.rpartition("/")
        if not name:
            raise ProviderRejected(f"{path!r} does not name a file")
        inspected = await self._engine.inspect_container(
            instance.provider_id, deadline_at=deadline_at
        )
        if inspected is None:
            raise ProviderGone(f"sandbox container {instance.provider_id} is gone")
        # The archive carries the directory as well as the file, and is
        # unpacked one level up. `put_archive` refuses a destination that does
        # not exist -- and the directories credentials go into are made by the
        # processes that read them, which have not necessarily run yet. Letting
        # tar create it is what makes delivery work on a container that has
        # only just started.
        parent, _, leaf = directory.rpartition("/")
        await self._engine.put_archive(
            inspected.container_id,
            parent or "/",
            _one_file_archive(name, value, directory=leaf or None),
            deadline_at=deadline_at,
        )


def _token_archive(token: str) -> bytes:
    return _one_file_archive("token", token.encode())


def _one_file_archive(
    name: str, payload: bytes, *, directory: str | None = None
) -> bytes:
    """A tar holding one 0600 file owned by the sandbox user.

    `put_archive` is the only way to place a file in a container without
    starting a process, which is what keeps a credential out of argv and out of
    the environment. The uid is the image's own user, so the file is readable by
    the sandbox and by nothing else in it.
    """
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        if directory:
            # 0700: the directory a credential sits in is no more readable
            # than the credential.
            folder = tarfile.TarInfo(name=directory)
            folder.type = tarfile.DIRTYPE
            folder.mode = 0o700
            folder.mtime = 0
            folder.uid = 10001
            folder.gid = 10001
            archive.addfile(folder)
        info = tarfile.TarInfo(name=f"{directory}/{name}" if directory else name)
        info.size = len(payload)
        info.mode = 0o600
        info.mtime = 0
        info.uid = 10001
        info.gid = 10001
        archive.addfile(info, BytesIO(payload))
    return buffer.getvalue()
