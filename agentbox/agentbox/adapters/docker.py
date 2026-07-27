from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
from io import BytesIO
import tarfile

import httpx

from agentbox.domain import (
    ByteRange,
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    FileStat,
    PortProtocol,
    ProcessRef,
    ProcessOutputSnapshot,
    PythonResult,
    PythonSessionRef,
    SandboxProfileRef,
    StorageKind,
    TerminalSize,
    WorkloadKind,
)
from agentbox.ports import (
    ProviderAllocationFailed,
    ProviderAllocationRef,
    ProviderCreateAmbiguous,
    ProviderCreateRejected,
    ProviderCreateRequest,
    ProviderCreateResult,
    ProviderFilesystemConflict,
    ProviderFilesystemNotFound,
    ProviderFilesystemRejected,
    ProviderFilesystemUnavailable,
    ProviderInventoryAllocation,
    ProviderMetadataEntry,
    ProviderNotReady,
    ProviderLifecycleError,
    ProviderProcessStartAmbiguous,
    ProviderProcessStartRejected,
    ProviderProcessStartRequest,
    ProviderProcessStartResult,
    ProviderPortTarget,
    ProviderPythonExecutionAmbiguous,
    ProviderPythonExecutionRejected,
    ProviderPythonSessionCreateAmbiguous,
    ProviderPythonSessionCreateRejected,
    ProviderPythonSessionCreateResult,
    ProviderReadyResult,
    ProviderStorageResult,
)
from agentbox.profiles import ProfileRegistry

from .docker_engine import (
    DockerContainerCreateRequest,
    DockerContainerInspect,
    DockerEmptyObject,
    DockerEngineClient,
    DockerEngineError,
    DockerHostConfig,
    DockerPortBinding,
    DockerRequestAmbiguous,
    DockerVolume,
    DockerVolumeCreateRequest,
)
from .workspace_runtime_client import (
    WorkspaceRuntimeClient,
    WorkspaceRuntimeError,
    WorkspaceRuntimeFileConflict,
    WorkspaceRuntimeFileNotFound,
    WorkspaceRuntimeFileRejected,
    WorkspaceRuntimePythonAmbiguous,
    WorkspaceRuntimeStartAmbiguous,
)


@dataclass(frozen=True, slots=True)
class DockerAdapterConfig:
    scope: str
    allow_mutable_images: bool = False
    memory_bytes: int = 2 * 1024 * 1024 * 1024
    nano_cpus: int = 1_000_000_000
    function_memory_bytes: int = 2 * 1024 * 1024 * 1024
    function_nano_cpus: int = 4_000_000_000
    pids_limit: int = 512
    add_host_gateway: bool = False
    host_alias: str | None = None
    private_network: str | None = None
    process_start_observation_seconds: float = 10.0
    max_file_transfer_bytes: int = 256 * 1024 * 1024

    def __post_init__(self) -> None:
        if (
            min(
                self.memory_bytes,
                self.nano_cpus,
                self.function_memory_bytes,
                self.function_nano_cpus,
            )
            < 1
        ):
            raise ValueError("Docker memory and CPU limits must be positive")
        if self.max_file_transfer_bytes < 1:
            raise ValueError("Docker filesystem transfer limit must be positive")
        if self.add_host_gateway and not self.host_alias:
            raise ValueError(
                "Docker host alias is required when host-gateway injection is enabled"
            )


@dataclass(frozen=True, slots=True)
class RuntimeCredentialSigner:
    key: bytes

    def __post_init__(self) -> None:
        if len(self.key) < 32:
            raise ValueError("runtime credential signing key must be at least 32 bytes")

    def token(self, provider_id: str) -> str:
        digest = hmac.new(self.key, provider_id.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")


class DockerSandboxAdapter:
    name = "docker"
    workspace_storage_kind = StorageKind.VOLUME

    def __init__(
        self,
        engine: DockerEngineClient,
        profiles: ProfileRegistry,
        config: DockerAdapterConfig,
        runtime_credentials: RuntimeCredentialSigner | None = None,
    ) -> None:
        self._engine = engine
        self._profiles = profiles
        self._config = config
        self._runtime_credentials = runtime_credentials
        self.scope = config.scope

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        artifact = self._profiles.docker_artifact(
            request.profile, workload_kind=request.key.workload_kind
        )
        if not self._config.allow_mutable_images and "@sha256:" not in artifact.image:
            raise ProviderCreateRejected(
                "Docker profile image must be pinned by sha256 digest"
            )

        labels = {entry.name: entry.value for entry in request.metadata}
        labels["allocation-token"] = str(request.allocation_token)
        workspace_storage: ProviderStorageResult | None = None
        binds: tuple[str, ...] = ()
        if request.workspace_storage is not None:
            volume_name = (
                request.workspace_storage.provider_storage_id
                or f"ab-ws-{request.workspace_storage.storage_token.hex}"
            )
            storage_labels = {
                "managed-by": "agentbox",
                "workload-kind": WorkloadKind.WORKSPACE.value,
                "logical-id": str(request.key.logical_id),
                "storage-token": str(request.workspace_storage.storage_token),
            }
            try:
                volume = await self._ensure_volume(
                    volume_name,
                    labels=storage_labels,
                    deadline_at=request.deadline_at,
                )
            except DockerEngineError as exc:
                raise ProviderCreateRejected(str(exc)) from exc
            workspace_storage = ProviderStorageResult(
                provider_storage_id=volume.name,
                bound_to_allocation=False,
            )
            labels["workspace-storage-id"] = volume.name
            binds = (f"{volume.name}:/workspace",)

        exposed_ports = {
            f"{port}/tcp": DockerEmptyObject() for port in artifact.published_ports
        }
        port_bindings = (
            {}
            if self._config.private_network
            else {
                f"{port}/tcp": (DockerPortBinding(host_ip="127.0.0.1", host_port=""),)
                for port in artifact.published_ports
            }
        )
        is_function = request.key.workload_kind == WorkloadKind.FUNCTION
        tmpfs: dict[str, str] = {}
        if is_function:
            tmpfs["/tmp"] = "rw,noexec,nosuid,size=512m"
            # Native wheels in verified function artifacts must mmap executable
            # segments. Keep general /tmp noexec and provide one private,
            # ephemeral executable mount only for the content-addressed cache.
            tmpfs["/run/lemma-function-cache"] = (
                "rw,exec,nosuid,nodev,size=512m,mode=0700,uid=10001,gid=10001"
            )
        host_config = DockerHostConfig(
            binds=binds,
            port_bindings=port_bindings,
            memory=(
                self._config.function_memory_bytes
                if is_function
                else self._config.memory_bytes
            ),
            nano_cpus=(
                self._config.function_nano_cpus
                if is_function
                else self._config.nano_cpus
            ),
            pids_limit=self._config.pids_limit,
            # Function control state lives entirely in /tmp. Keeping the image
            # root read-only catches accidental writes and enforces the same
            # stateless contract used by production providers.
            readonly_rootfs=is_function,
            tmpfs=tmpfs,
            extra_hosts=(
                (f"{self._config.host_alias}:host-gateway",)
                if self._config.add_host_gateway
                else ()
            ),
            network_mode=self._config.private_network,
        )
        create_body = DockerContainerCreateRequest(
            image=artifact.image,
            command=artifact.command or None,
            labels=labels,
            exposed_ports=exposed_ports,
            host_config=host_config,
            working_dir="/workspace" if not is_function else "/tmp",
            env=tuple(
                item
                for item in (
                    f"AGENTBOX_MAX_FILE_TRANSFER_BYTES={self._config.max_file_transfer_bytes}",
                    (
                        "LEMMA_FUNCTION_CACHE_ROOT=/run/lemma-function-cache"
                        if is_function
                        else None
                    ),
                )
                if item is not None
            ),
        )
        try:
            created = await self._engine.create_container(
                self._container_name(request),
                create_body,
                deadline_at=request.deadline_at,
            )
        except DockerRequestAmbiguous as exc:
            raise ProviderCreateAmbiguous(str(exc)) from exc
        except DockerEngineError as exc:
            raise ProviderCreateRejected(str(exc)) from exc
        return ProviderCreateResult(
            provider_id=created.container_id,
            provider_instance_id=created.container_id,
            provider_request_id=None,
            workspace_storage=workspace_storage,
        )

    async def wait_ready(
        self,
        allocation: ProviderAllocationRef,
        *,
        profile: SandboxProfileRef,
        deadline_at: datetime,
    ) -> ProviderReadyResult:
        try:
            await self._engine.start_container(
                allocation.provider_id, deadline_at=deadline_at
            )
            while datetime.now(timezone.utc) < deadline_at:
                inspected = await self._engine.inspect_container(
                    allocation.provider_id, deadline_at=deadline_at
                )
                if inspected is None:
                    raise ProviderAllocationFailed("Docker container disappeared")
                if inspected.state.running:
                    break
                if inspected.state.status in {"dead", "exited"}:
                    raise ProviderAllocationFailed(
                        "Docker container exited before readiness "
                        f"(exit={inspected.state.exit_code})"
                    )
                await asyncio.sleep(0.05)
            else:
                raise ProviderNotReady(
                    "Docker container is still starting", retry_after_ms=250
                )

            workload_kind = WorkloadKind(inspected.config.labels["workload-kind"])
            if (
                inspected.config.labels["profile-name"] != profile.name
                or inspected.config.labels["profile-digest"] != profile.digest
            ):
                raise ProviderAllocationFailed(
                    "Docker container profile metadata does not match allocation"
                )
            artifact = self._profiles.docker_artifact(
                profile,
                workload_kind=workload_kind,
            )
            if inspected.config.image != artifact.image:
                raise ProviderAllocationFailed(
                    "Docker container image does not match profile artifact"
                )
            if workload_kind == WorkloadKind.WORKSPACE:
                self._validate_workspace_mount(inspected)
            if artifact.runtime_port is not None:
                if workload_kind == WorkloadKind.FUNCTION:
                    await self._wait_function_runtime_ready(
                        inspected,
                        runtime_port=artifact.runtime_port,
                        deadline_at=deadline_at,
                    )
                else:
                    await self._wait_runtime_ready(
                        inspected,
                        runtime_port=artifact.runtime_port,
                        deadline_at=deadline_at,
                    )
                    await self._verify_workspace_filesystem(
                        inspected,
                        allocation,
                        runtime_port=artifact.runtime_port,
                        deadline_at=deadline_at,
                    )
            if artifact.readiness_argv:
                exit_code = await self._engine.run_exec(
                    allocation.provider_id,
                    artifact.readiness_argv,
                    working_dir=(
                        "/tmp"
                        if workload_kind == WorkloadKind.FUNCTION
                        else "/workspace"
                    ),
                    deadline_at=deadline_at,
                )
                if exit_code != 0:
                    raise ProviderAllocationFailed(
                        f"Docker readiness command failed with exit code {exit_code}"
                    )
        except ProviderAllocationFailed:
            raise
        except ProviderNotReady:
            raise
        except (DockerEngineError, KeyError, ValueError) as exc:
            raise ProviderAllocationFailed(str(exc)) from exc
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_id,
        )

    @staticmethod
    def _validate_workspace_mount(inspected: DockerContainerInspect) -> None:
        storage_id = inspected.config.labels.get("workspace-storage-id")
        if not storage_id:
            raise ProviderAllocationFailed(
                "Docker workspace has no persisted storage identity"
            )
        mounted_storage_ids = {
            source
            for binding in inspected.host_config.binds
            for source, separator, target_and_mode in (binding.partition(":"),)
            if separator
            and target_and_mode.split(":", 1)[0].rstrip("/") == "/workspace"
        }
        if mounted_storage_ids != {storage_id}:
            raise ProviderAllocationFailed(
                "Docker workspace mount does not match persisted storage identity"
            )

    async def _verify_workspace_filesystem(
        self,
        inspected: DockerContainerInspect,
        allocation: ProviderAllocationRef,
        *,
        runtime_port: int,
        deadline_at: datetime,
    ) -> None:
        if self._runtime_credentials is None:  # pragma: no cover - checked earlier
            raise ProviderAllocationFailed(
                "Docker workspace runtime credentials are not configured"
            )
        marker = f"/workspace/.agentbox-readiness-{allocation.allocation_token.hex}"
        payload = allocation.allocation_token.bytes

        async def chunks() -> AsyncIterator[bytes]:
            yield payload

        client = self._runtime_client_from_inspect(
            inspected,
            runtime_port=runtime_port,
            token=self._runtime_credentials.token(inspected.container_id),
            private_network=self._config.private_network,
        )
        try:
            await client.write_file(
                marker,
                chunks(),
                expected_sha256=None,
                deadline_at=deadline_at,
            )
            stream = await client.open_file(
                marker,
                ByteRange(offset=0, length=None),
                deadline_at=deadline_at,
            )
            observed = b"".join([chunk async for chunk in stream])
            if observed != payload:
                raise ProviderAllocationFailed(
                    "Docker workspace filesystem readiness marker did not round-trip"
                )
        except WorkspaceRuntimeError as exc:
            raise ProviderAllocationFailed(str(exc)) from exc
        finally:
            try:
                await client.delete_file(
                    marker,
                    recursive=False,
                    deadline_at=deadline_at,
                )
            except WorkspaceRuntimeError:
                pass
            await client.close()

    async def start_process(
        self, request: ProviderProcessStartRequest
    ) -> ProviderProcessStartResult:
        client = await self._runtime_client(
            request.allocation.provider_id,
            deadline_at=request.request.deadline_at,
        )
        try:
            response = await client.start_process(request.request)
        except WorkspaceRuntimeStartAmbiguous as exc:
            raise ProviderProcessStartAmbiguous(str(exc)) from exc
        except WorkspaceRuntimeError as exc:
            raise ProviderProcessStartRejected(str(exc)) from exc
        finally:
            await client.close()
        return ProviderProcessStartResult(
            provider_process_id=str(response.operation_id),
            provider_tag=str(response.operation_id),
        )

    async def send_process_input(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        data: bytes,
        deadline_at: datetime,
    ) -> None:
        client = await self._runtime_client(
            allocation.provider_id, deadline_at=deadline_at
        )
        try:
            await client.send_input(
                self._process_id(process), data, deadline_at=deadline_at
            )
        finally:
            await client.close()

    async def read_process_output(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        after_sequence: int,
        wait_seconds: float,
        deadline_at: datetime,
    ) -> ProcessOutputSnapshot:
        client = await self._runtime_client(
            allocation.provider_id, deadline_at=deadline_at
        )
        try:
            return await client.read_output(
                self._process_id(process),
                after_sequence=after_sequence,
                wait_seconds=wait_seconds,
                deadline_at=deadline_at,
            )
        finally:
            await client.close()

    async def resize_process(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        size: TerminalSize,
        deadline_at: datetime,
    ) -> None:
        client = await self._runtime_client(
            allocation.provider_id, deadline_at=deadline_at
        )
        try:
            await client.resize(
                self._process_id(process), size, deadline_at=deadline_at
            )
        finally:
            await client.close()

    async def terminate_process(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        grace_seconds: float,
        deadline_at: datetime,
    ) -> None:
        client = await self._runtime_client(
            allocation.provider_id, deadline_at=deadline_at
        )
        try:
            await client.terminate(
                self._process_id(process),
                grace_seconds=grace_seconds,
                deadline_at=deadline_at,
            )
        finally:
            await client.close()

    async def stat_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        deadline_at: datetime,
    ) -> FileStat:
        async with self._filesystem_client(
            allocation.provider_id, deadline_at=deadline_at
        ) as client:
            return await client.stat_file(path, deadline_at=deadline_at)

    async def create_directory(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        deadline_at: datetime,
    ) -> None:
        async with self._filesystem_client(
            allocation.provider_id, deadline_at=deadline_at
        ) as client:
            await client.create_directory(path, deadline_at=deadline_at)

    async def list_files(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        deadline_at: datetime,
    ) -> tuple[FileStat, ...]:
        async with self._filesystem_client(
            allocation.provider_id, deadline_at=deadline_at
        ) as client:
            return await client.list_files(path, deadline_at=deadline_at)

    async def open_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        byte_range: ByteRange,
        deadline_at: datetime,
    ) -> AsyncIterator[bytes]:
        client: WorkspaceRuntimeClient | None = None
        try:
            client = await self._runtime_client(
                allocation.provider_id, deadline_at=deadline_at
            )
            stream = await client.open_file(path, byte_range, deadline_at=deadline_at)
        except WorkspaceRuntimeFileNotFound as exc:
            if client is not None:
                await client.close()
            raise ProviderFilesystemNotFound(str(exc)) from exc
        except WorkspaceRuntimeFileConflict as exc:
            if client is not None:
                await client.close()
            raise ProviderFilesystemConflict(str(exc)) from exc
        except WorkspaceRuntimeFileRejected as exc:
            if client is not None:
                await client.close()
            raise ProviderFilesystemRejected(
                str(exc), status_code=exc.status_code
            ) from exc
        except (WorkspaceRuntimeError, DockerEngineError) as exc:
            if client is not None:
                await client.close()
            raise ProviderFilesystemUnavailable(str(exc)) from exc

        async def chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in stream:
                    yield chunk
            except WorkspaceRuntimeError as exc:
                raise ProviderFilesystemUnavailable(str(exc)) from exc
            finally:
                await client.close()

        return chunks()

    async def write_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        data: AsyncIterable[bytes],
        expected_sha256: str | None,
        deadline_at: datetime,
    ) -> FileStat:
        async with self._filesystem_client(
            allocation.provider_id, deadline_at=deadline_at
        ) as client:
            return await client.write_file(
                path,
                data,
                expected_sha256=expected_sha256,
                deadline_at=deadline_at,
            )

    async def move_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        source: str,
        destination: str,
        deadline_at: datetime,
    ) -> None:
        async with self._filesystem_client(
            allocation.provider_id, deadline_at=deadline_at
        ) as client:
            await client.move_file(source, destination, deadline_at=deadline_at)

    async def delete_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        recursive: bool,
        deadline_at: datetime,
    ) -> bool:
        try:
            async with self._filesystem_client(
                allocation.provider_id, deadline_at=deadline_at
            ) as client:
                await client.delete_file(
                    path, recursive=recursive, deadline_at=deadline_at
                )
                return True
        except ProviderFilesystemNotFound:
            return False

    async def create_python_session(
        self,
        allocation: ProviderAllocationRef,
        request: CreatePythonSessionRequest,
    ) -> ProviderPythonSessionCreateResult:
        client = await self._runtime_client(
            allocation.provider_id, deadline_at=request.deadline_at
        )
        try:
            response = await client.create_python_session(request)
        except WorkspaceRuntimePythonAmbiguous as exc:
            raise ProviderPythonSessionCreateAmbiguous(str(exc)) from exc
        except WorkspaceRuntimeError as exc:
            raise ProviderPythonSessionCreateRejected(str(exc)) from exc
        finally:
            await client.close()
        return ProviderPythonSessionCreateResult(
            provider_context_id=str(response.session_id)
        )

    async def execute_python(
        self,
        allocation: ProviderAllocationRef,
        session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult:
        client = await self._runtime_client(
            allocation.provider_id, deadline_at=request.deadline_at
        )
        try:
            return await client.execute_python(session, request)
        except WorkspaceRuntimePythonAmbiguous as exc:
            raise ProviderPythonExecutionAmbiguous(str(exc)) from exc
        except WorkspaceRuntimeError as exc:
            raise ProviderPythonExecutionRejected(str(exc)) from exc
        finally:
            await client.close()

    async def restart_python_session(
        self,
        allocation: ProviderAllocationRef,
        session: PythonSessionRef,
        *,
        deadline_at: datetime,
    ) -> ProviderPythonSessionCreateResult:
        client = await self._runtime_client(
            allocation.provider_id, deadline_at=deadline_at
        )
        try:
            response = await client.restart_python_session(
                str(session.session_id), deadline_at=deadline_at
            )
        except WorkspaceRuntimePythonAmbiguous as exc:
            raise ProviderPythonSessionCreateAmbiguous(str(exc)) from exc
        except WorkspaceRuntimeError as exc:
            raise ProviderPythonSessionCreateRejected(str(exc)) from exc
        finally:
            await client.close()
        return ProviderPythonSessionCreateResult(
            provider_context_id=str(response.session_id)
        )

    async def delete_python_session(
        self,
        allocation: ProviderAllocationRef,
        session: PythonSessionRef,
        *,
        deadline_at: datetime,
    ) -> None:
        client = await self._runtime_client(
            allocation.provider_id, deadline_at=deadline_at
        )
        try:
            await client.delete_python_session(
                str(session.session_id), deadline_at=deadline_at
            )
        finally:
            await client.close()

    async def resolve_port_target(
        self,
        allocation: ProviderAllocationRef,
        *,
        port: int,
        protocol: PortProtocol,
        deadline_at: datetime,
        activity_until: datetime | None = None,
    ) -> ProviderPortTarget:
        del activity_until
        inspected = await self._engine.inspect_container(
            allocation.provider_id, deadline_at=deadline_at
        )
        if inspected is None or not inspected.state.running:
            raise ProviderLifecycleError("Docker allocation is not running")
        if self._config.private_network:
            attachment = inspected.network_settings.networks.get(
                self._config.private_network
            )
            if attachment is None or not attachment.ip_address:
                raise ProviderLifecycleError(
                    "Docker allocation is not attached to the configured private network"
                )
            return ProviderPortTarget(
                base_url=f"{protocol.value}://{attachment.ip_address}:{port}"
            )
        bindings = inspected.network_settings.ports.get(f"{port}/tcp")
        if not bindings:
            raise ProviderLifecycleError(
                f"Docker profile does not publish sandbox port {port}"
            )
        return ProviderPortTarget(
            base_url=f"{protocol.value}://127.0.0.1:{bindings[0].host_port}"
        )

    async def release_allocation(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> None:
        try:
            if allocation.key.workload_kind == WorkloadKind.FUNCTION:
                await self._engine.stop_container(
                    allocation.provider_id,
                    deadline_at=deadline_at,
                    grace_seconds=5,
                )
                return
            client = await self._runtime_client(
                allocation.provider_id, deadline_at=deadline_at
            )
            try:
                await client.quiesce(deadline_at=deadline_at)
            finally:
                await client.close()
            await self._engine.stop_container(
                allocation.provider_id,
                deadline_at=deadline_at,
                grace_seconds=5,
            )
        except (DockerEngineError, WorkspaceRuntimeError) as exc:
            raise ProviderLifecycleError(str(exc)) from exc

    async def destroy_allocation(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> None:
        try:
            await self._engine.delete_container(
                allocation.provider_id, deadline_at=deadline_at, force=True
            )
        except DockerEngineError as exc:
            raise ProviderLifecycleError(str(exc)) from exc

    async def destroy_workspace_storage(
        self,
        provider_storage_id: str,
        *,
        deadline_at: datetime,
    ) -> None:
        try:
            await self._engine.delete_volume(
                provider_storage_id, deadline_at=deadline_at
            )
        except DockerEngineError as exc:
            raise ProviderLifecycleError(str(exc)) from exc

    async def find_allocations(
        self,
        metadata: tuple[ProviderMetadataEntry, ...],
        *,
        deadline_at: datetime,
    ) -> tuple[ProviderInventoryAllocation, ...]:
        expected = {item.name: item.value for item in metadata}
        try:
            containers = await self._engine.list_containers(
                labels=expected, deadline_at=deadline_at
            )
        except DockerEngineError as exc:
            raise ProviderLifecycleError(str(exc)) from exc
        matches: list[ProviderInventoryAllocation] = []
        for container in containers:
            if any(
                container.labels.get(name) != value for name, value in expected.items()
            ):
                continue
            storage_id = container.labels.get("workspace-storage-id")
            matches.append(
                ProviderInventoryAllocation(
                    provider_id=container.container_id,
                    provider_instance_id=container.container_id,
                    workspace_storage=(
                        ProviderStorageResult(
                            provider_storage_id=storage_id,
                            bound_to_allocation=False,
                        )
                        if storage_id is not None
                        else None
                    ),
                )
            )
        return tuple(matches)

    async def close(self) -> None:
        await self._engine.close()

    async def _ensure_volume(
        self,
        name: str,
        *,
        labels: dict[str, str],
        deadline_at: datetime,
    ) -> DockerVolume:
        existing = await self._engine.inspect_volume(name, deadline_at=deadline_at)
        if existing is not None:
            for key, value in labels.items():
                if existing.labels.get(key) != value:
                    raise DockerEngineError(
                        f"Docker volume {name} has conflicting ownership metadata"
                    )
            return existing
        return await self._engine.create_volume(
            DockerVolumeCreateRequest(name=name, labels=labels),
            deadline_at=deadline_at,
        )

    async def _wait_runtime_ready(
        self,
        inspected: DockerContainerInspect,
        *,
        runtime_port: int,
        deadline_at: datetime,
    ) -> None:
        if self._runtime_credentials is None:
            raise ProviderAllocationFailed(
                "Docker workspace runtime credentials are not configured"
            )
        token = self._runtime_credentials.token(inspected.container_id)
        client = self._runtime_client_from_inspect(
            inspected,
            runtime_port=runtime_port,
            token=token,
            private_network=self._config.private_network,
            request_timeout_seconds=0.25,
        )
        try:
            try:
                await client.health(deadline_at=deadline_at)
                return
            except WorkspaceRuntimeError:
                pass
            await self._engine.put_archive(
                inspected.container_id,
                "/run/agentbox-bootstrap",
                self._token_archive(token),
                deadline_at=deadline_at,
            )
            while datetime.now(timezone.utc) < deadline_at:
                try:
                    await client.health(deadline_at=deadline_at)
                    return
                except WorkspaceRuntimeError:
                    await asyncio.sleep(0.05)
            raise ProviderNotReady(
                "Docker workspace runtime is still starting", retry_after_ms=250
            )
        finally:
            await client.close()

    async def _wait_function_runtime_ready(
        self,
        inspected: DockerContainerInspect,
        *,
        runtime_port: int,
        deadline_at: datetime,
    ) -> None:
        base_url = self._runtime_base_url(
            inspected,
            runtime_port=runtime_port,
            private_network=self._config.private_network,
        )
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(0.25),
            follow_redirects=False,
        ) as client:
            while datetime.now(timezone.utc) < deadline_at:
                try:
                    response = await client.get("/healthz")
                    if response.status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.05)
        raise ProviderNotReady(
            "Docker function runtime is still starting", retry_after_ms=250
        )

    @asynccontextmanager
    async def _filesystem_client(
        self, provider_id: str, *, deadline_at: datetime
    ) -> AsyncIterator[WorkspaceRuntimeClient]:
        client: WorkspaceRuntimeClient | None = None
        try:
            client = await self._runtime_client(provider_id, deadline_at=deadline_at)
            yield client
        except WorkspaceRuntimeFileNotFound as exc:
            raise ProviderFilesystemNotFound(str(exc)) from exc
        except WorkspaceRuntimeFileConflict as exc:
            raise ProviderFilesystemConflict(str(exc)) from exc
        except WorkspaceRuntimeFileRejected as exc:
            raise ProviderFilesystemRejected(
                str(exc), status_code=exc.status_code
            ) from exc
        except (WorkspaceRuntimeError, DockerEngineError) as exc:
            raise ProviderFilesystemUnavailable(str(exc)) from exc
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
            raise WorkspaceRuntimeError("Docker container does not exist")
        profile = SandboxProfileRef(
            name=inspected.config.labels["profile-name"],
            digest=inspected.config.labels["profile-digest"],
        )
        workload_kind = WorkloadKind(inspected.config.labels["workload-kind"])
        artifact = self._profiles.docker_artifact(profile, workload_kind=workload_kind)
        if artifact.runtime_port is None:
            raise WorkspaceRuntimeError("profile has no workspace runtime")
        if self._runtime_credentials is None:
            raise WorkspaceRuntimeError("runtime credentials are not configured")
        return self._runtime_client_from_inspect(
            inspected,
            runtime_port=artifact.runtime_port,
            token=self._runtime_credentials.token(provider_id),
            private_network=self._config.private_network,
        )

    @staticmethod
    def _runtime_client_from_inspect(
        inspected: DockerContainerInspect,
        *,
        runtime_port: int,
        token: str,
        private_network: str | None = None,
        request_timeout_seconds: float = 35,
    ) -> WorkspaceRuntimeClient:
        base_url = DockerSandboxAdapter._runtime_base_url(
            inspected,
            runtime_port=runtime_port,
            private_network=private_network,
        )
        return WorkspaceRuntimeClient(
            base_url,
            token,
            request_timeout_seconds=request_timeout_seconds,
        )

    @staticmethod
    def _runtime_base_url(
        inspected: DockerContainerInspect,
        *,
        runtime_port: int,
        private_network: str | None,
    ) -> str:
        if private_network:
            attachment = inspected.network_settings.networks.get(private_network)
            if attachment is None or not attachment.ip_address:
                raise WorkspaceRuntimeError(
                    "Docker runtime is not attached to the configured private network"
                )
            return f"http://{attachment.ip_address}:{runtime_port}"
        bindings = inspected.network_settings.ports.get(f"{runtime_port}/tcp")
        if not bindings:
            raise WorkspaceRuntimeError("Docker runtime port is not published")
        return f"http://127.0.0.1:{bindings[0].host_port}"

    @staticmethod
    def _token_archive(token: str) -> bytes:
        buffer = BytesIO()
        payload = token.encode()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo(name="token")
            info.size = len(payload)
            info.mode = 0o600
            info.mtime = 0
            info.uid = 10001
            info.gid = 10001
            archive.addfile(info, BytesIO(payload))
        return buffer.getvalue()

    @staticmethod
    def _container_name(request: ProviderCreateRequest) -> str:
        kind = "w" if request.key.workload_kind == WorkloadKind.WORKSPACE else "f"
        return (
            f"ab-{kind}-{request.key.logical_id.hex[:12]}-"
            f"{request.allocation_id.hex[:12]}"
        )

    @staticmethod
    def _process_id(process: ProcessRef) -> str:
        if process.provider_process_id is None:
            raise WorkspaceRuntimeError("process has no provider identity")
        return process.provider_process_id
