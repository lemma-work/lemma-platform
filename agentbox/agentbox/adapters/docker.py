from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from io import BytesIO
import tarfile
import time
from uuid import uuid4

from agentbox.domain import (
    ByteRange,
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    FileStat,
    PortProtocol,
    ProcessOutputChannel,
    ProcessOutputChunk,
    ProcessRef,
    ProcessOutputSnapshot,
    ProcessState,
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
from agentbox.function_runtime.process_protocol import (
    ProcessInspection,
    ProcessManifest,
    ProcessStateRecord,
    RuntimeEnvironmentVariable,
)

from .docker_engine import (
    DockerContainerCreateRequest,
    DockerContainerInspect,
    DockerEmptyObject,
    DockerEngineClient,
    DockerEngineError,
    DockerExecResult,
    DockerExecCreateRequest,
    DockerExecStartRequest,
    DockerHostConfig,
    DockerPortBinding,
    DockerRequestAmbiguous,
    DockerVolume,
    DockerVolumeCreateRequest,
)
from .workspace_runtime_client import (
    WorkspaceRuntimeClient,
    WorkspaceRuntimeError,
    WorkspaceRuntimePythonAmbiguous,
    WorkspaceRuntimeStartAmbiguous,
)


@dataclass(frozen=True, slots=True)
class DockerAdapterConfig:
    scope: str
    allow_mutable_images: bool = False
    memory_bytes: int = 2 * 1024 * 1024 * 1024
    nano_cpus: int = 1_000_000_000
    pids_limit: int = 512
    add_host_gateway: bool = False
    private_network: str | None = None
    process_start_observation_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class RuntimeCredentialSigner:
    key: bytes

    def __post_init__(self) -> None:
        if len(self.key) < 32:
            raise ValueError("runtime credential signing key must be at least 32 bytes")

    def token(self, provider_id: str) -> str:
        digest = hmac.new(self.key, provider_id.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")


@dataclass(frozen=True, slots=True)
class _FunctionProcessFiles:
    state: ProcessStateRecord | None
    chunks: tuple[ProcessOutputChunk, ...]


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
        host_config = DockerHostConfig(
            binds=binds,
            port_bindings=port_bindings,
            memory=self._config.memory_bytes,
            nano_cpus=self._config.nano_cpus,
            pids_limit=self._config.pids_limit,
            # Function control state lives entirely in /tmp. Keeping the image
            # root read-only catches accidental writes and enforces the same
            # stateless contract used by production providers.
            readonly_rootfs=is_function,
            tmpfs=tmpfs,
            extra_hosts=(
                ("host.docker.internal:host-gateway",)
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
            if artifact.runtime_port is not None:
                await self._wait_runtime_ready(
                    inspected,
                    runtime_port=artifact.runtime_port,
                    deadline_at=deadline_at,
                )
            exit_code = await self._engine.run_exec(
                allocation.provider_id,
                artifact.readiness_argv,
                working_dir=(
                    "/tmp" if workload_kind == WorkloadKind.FUNCTION else "/workspace"
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

    async def start_process(
        self, request: ProviderProcessStartRequest
    ) -> ProviderProcessStartResult:
        if request.process.key.workload_kind == WorkloadKind.FUNCTION:
            return await self._start_function_process(request)
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
        if allocation.key.workload_kind == WorkloadKind.FUNCTION:
            await self._send_function_process_input(
                allocation,
                process=process,
                data=data,
                deadline_at=deadline_at,
            )
            return
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
        if allocation.key.workload_kind == WorkloadKind.FUNCTION:
            return await self._read_function_process_output(
                allocation,
                process=process,
                after_sequence=after_sequence,
                wait_seconds=wait_seconds,
                deadline_at=deadline_at,
            )
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
        if allocation.key.workload_kind == WorkloadKind.FUNCTION:
            raise ProviderLifecycleError(
                "function profile does not support terminal resize"
            )
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
        if allocation.key.workload_kind == WorkloadKind.FUNCTION:
            await self._terminate_function_process(
                allocation,
                process=process,
                grace_seconds=grace_seconds,
                deadline_at=deadline_at,
            )
            return
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
        client = await self._runtime_client(
            allocation.provider_id, deadline_at=deadline_at
        )
        try:
            return await client.stat_file(path, deadline_at=deadline_at)
        finally:
            await client.close()

    async def list_files(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        deadline_at: datetime,
    ) -> tuple[FileStat, ...]:
        client = await self._runtime_client(
            allocation.provider_id, deadline_at=deadline_at
        )
        try:
            return await client.list_files(path, deadline_at=deadline_at)
        finally:
            await client.close()

    async def read_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        byte_range: ByteRange,
        deadline_at: datetime,
    ) -> bytes:
        client = await self._runtime_client(
            allocation.provider_id, deadline_at=deadline_at
        )
        try:
            return await client.read_file(path, byte_range, deadline_at=deadline_at)
        finally:
            await client.close()

    async def write_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        data: bytes,
        expected_sha256: str | None,
        deadline_at: datetime,
    ) -> FileStat:
        client = await self._runtime_client(
            allocation.provider_id, deadline_at=deadline_at
        )
        try:
            return await client.write_file(
                path,
                data,
                expected_sha256=expected_sha256,
                deadline_at=deadline_at,
            )
        finally:
            await client.close()

    async def move_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        source: str,
        destination: str,
        deadline_at: datetime,
    ) -> None:
        client = await self._runtime_client(
            allocation.provider_id, deadline_at=deadline_at
        )
        try:
            await client.move_file(source, destination, deadline_at=deadline_at)
        finally:
            await client.close()

    async def delete_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        recursive: bool,
        deadline_at: datetime,
    ) -> bool:
        client = await self._runtime_client(
            allocation.provider_id, deadline_at=deadline_at
        )
        try:
            await client.delete_file(path, recursive=recursive, deadline_at=deadline_at)
            return True
        finally:
            await client.close()

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
    ) -> ProviderPortTarget:
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

    async def _start_function_process(
        self, request: ProviderProcessStartRequest
    ) -> ProviderProcessStartResult:
        if request.request.tty is not None:
            raise ProviderProcessStartRejected(
                "function sandboxes do not support terminal processes"
            )
        manifest = ProcessManifest(
            operation_id=request.process.operation_id,
            shell_command=request.request.shell_command,
            argv=request.request.argv,
            cwd=request.request.cwd,
            environment=tuple(
                RuntimeEnvironmentVariable(name=item.name, value=item.value)
                for item in request.request.environment
            ),
            output_limit_bytes=request.request.output_limit_bytes,
            deadline_at=request.request.deadline_at,
        )
        try:
            created = await self._engine.create_exec(
                request.allocation.provider_id,
                DockerExecCreateRequest(
                    argv=(
                        "/usr/local/bin/python",
                        "-m",
                        "agentbox.function_runtime.process_supervisor",
                        str(request.process.operation_id),
                    ),
                    attach_stdin=False,
                    attach_stdout=False,
                    attach_stderr=False,
                    working_dir="/tmp",
                    env=(
                        "AGENTBOX_PROCESS_MANIFEST="
                        + base64.b64encode(
                            manifest.model_dump_json().encode()
                        ).decode(),
                    ),
                ),
                deadline_at=request.request.deadline_at,
            )
            try:
                await self._engine.start_exec(
                    created.exec_id,
                    DockerExecStartRequest(detach=True),
                    deadline_at=request.request.deadline_at,
                )
            except DockerRequestAmbiguous as exc:
                if not await self._wait_for_function_start(
                    request.allocation,
                    request.process,
                    deadline_at=request.request.deadline_at,
                ):
                    raise ProviderProcessStartAmbiguous(str(exc)) from exc
            else:
                if not await self._wait_for_function_start(
                    request.allocation,
                    request.process,
                    deadline_at=request.request.deadline_at,
                ):
                    inspected = await self._engine.inspect_exec(
                        created.exec_id, deadline_at=request.request.deadline_at
                    )
                    if not inspected.running:
                        diagnostic = await self._function_process_diagnostic(
                            request.allocation,
                            process=request.process,
                            deadline_at=request.request.deadline_at,
                        )
                        raise ProviderProcessStartRejected(
                            "function process supervisor exited before start "
                            f"(exit={inspected.exit_code})"
                            + (f": {diagnostic}" if diagnostic else "")
                        )
                    raise ProviderProcessStartAmbiguous(
                        "function process supervisor is running without a start record"
                    )
        except ProviderProcessStartAmbiguous:
            raise
        except DockerEngineError as exc:
            raise ProviderProcessStartRejected(str(exc)) from exc
        return ProviderProcessStartResult(
            provider_process_id=created.exec_id,
            provider_tag=str(request.process.operation_id),
        )

    async def _wait_for_function_start(
        self,
        allocation: ProviderAllocationRef,
        process: ProcessRef,
        *,
        deadline_at: datetime,
    ) -> bool:
        wait_until = min(
            deadline_at,
            datetime.now(timezone.utc)
            + timedelta(seconds=self._config.process_start_observation_seconds),
        )
        while datetime.now(timezone.utc) < wait_until:
            try:
                result = await self._engine.run_exec_capture(
                    allocation.provider_id,
                    (
                        "/usr/bin/test",
                        "-s",
                        f"/tmp/.agentbox/processes/{process.operation_id}/state.json",
                    ),
                    deadline_at=min(deadline_at, wait_until),
                )
            except DockerEngineError:
                result = None
            if result is not None and result.exit_code == 0:
                return True
            await asyncio.sleep(0.1)
        return False

    async def _send_function_process_input(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        data: bytes,
        deadline_at: datetime,
    ) -> None:
        path = self._function_process_directory(process)
        del path
        for index, offset in enumerate(range(0, len(data), 48 * 1024)):
            chunk = data[offset : offset + 48 * 1024]
            name = f"{time.time_ns():020d}-{index:06d}-{uuid4().hex}"
            result = await self._engine.run_exec_capture(
                allocation.provider_id,
                (
                    "/usr/local/bin/python",
                    "-m",
                    "agentbox.function_runtime.process_control",
                    "input",
                    str(process.operation_id),
                    name,
                ),
                environment=self._function_control_environment(input_data=chunk),
                deadline_at=deadline_at,
            )
            self._check_function_control_result(result)

    async def _read_function_process_output(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        after_sequence: int,
        wait_seconds: float,
        deadline_at: datetime,
    ) -> ProcessOutputSnapshot:
        wait_until = min(
            deadline_at,
            datetime.now(timezone.utc) + timedelta(seconds=wait_seconds),
        )
        while True:
            files = await self._function_process_files(
                allocation,
                process=process,
                after_sequence=after_sequence,
                deadline_at=deadline_at,
            )
            state = self._process_state(files.state)
            chunks = tuple(
                chunk for chunk in files.chunks if chunk.sequence >= after_sequence
            )
            if chunks or state in self._terminal_process_states() or not wait_seconds:
                return ProcessOutputSnapshot(
                    chunks=chunks,
                    next_sequence=(
                        files.state.next_sequence
                        if files.state is not None
                        else after_sequence
                    ),
                    truncated_before_sequence=(
                        files.state.truncated_before_sequence
                        if files.state is not None
                        else None
                    ),
                    state=state,
                    exit_code=(files.state.exit_code if files.state else None),
                )
            if datetime.now(timezone.utc) >= wait_until:
                return ProcessOutputSnapshot(
                    chunks=(),
                    next_sequence=(
                        files.state.next_sequence
                        if files.state is not None
                        else after_sequence
                    ),
                    truncated_before_sequence=(
                        files.state.truncated_before_sequence
                        if files.state is not None
                        else None
                    ),
                    state=state,
                    exit_code=(files.state.exit_code if files.state else None),
                )
            await asyncio.sleep(0.05)

    async def _terminate_function_process(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        grace_seconds: float,
        deadline_at: datetime,
    ) -> None:
        result = await self._engine.run_exec_capture(
            allocation.provider_id,
            (
                "/usr/local/bin/python",
                "-m",
                "agentbox.function_runtime.process_control",
                "cancel",
                str(process.operation_id),
                str(grace_seconds),
            ),
            deadline_at=deadline_at,
        )
        self._check_function_control_result(result)
        while datetime.now(timezone.utc) < deadline_at:
            files = await self._function_process_files(
                allocation, process=process, deadline_at=deadline_at
            )
            if self._process_state(files.state) in self._terminal_process_states():
                return
            await asyncio.sleep(0.02)
        raise ProviderLifecycleError(
            "function process did not terminate before the control deadline"
        )

    async def _function_process_files(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        after_sequence: int = 0,
        deadline_at: datetime,
    ) -> _FunctionProcessFiles:
        result = await self._engine.run_exec_capture(
            allocation.provider_id,
            (
                "/usr/local/bin/python",
                "-m",
                "agentbox.function_runtime.process_control",
                "inspect",
                str(process.operation_id),
                str(after_sequence),
            ),
            environment=self._function_control_environment(),
            deadline_at=deadline_at,
        )
        self._check_function_control_result(result)
        inspection = ProcessInspection.model_validate_json(result.stdout)
        return _FunctionProcessFiles(
            state=inspection.state,
            chunks=tuple(
                ProcessOutputChunk(
                    sequence=chunk.sequence,
                    channel=ProcessOutputChannel(chunk.channel),
                    data=base64.b64decode(chunk.data_base64, validate=True),
                )
                for chunk in inspection.chunks
            ),
        )

    async def _function_process_diagnostic(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        deadline_at: datetime,
    ) -> str:
        result = await self._engine.run_exec_capture(
            allocation.provider_id,
            (
                "/bin/bash",
                "-lc",
                f"cat /tmp/agentbox-supervisor-{process.operation_id}.log",
            ),
            environment=self._function_control_environment(),
            deadline_at=deadline_at,
        )
        return (result.stdout or result.stderr)[:4096].decode(errors="replace").strip()

    @staticmethod
    def _function_process_directory(process: ProcessRef) -> str:
        return f"/tmp/.agentbox/processes/{process.operation_id}"

    @staticmethod
    def _function_control_environment(
        *, input_data: bytes | None = None
    ) -> tuple[str, ...]:
        values: list[str] = []
        if input_data is not None:
            values.append(
                "AGENTBOX_PROCESS_INPUT=" + base64.b64encode(input_data).decode()
            )
        return tuple(values)

    @staticmethod
    def _check_function_control_result(result: DockerExecResult) -> None:
        if result.exit_code != 0:
            message = result.stderr.decode(errors="replace").strip()
            raise DockerEngineError(
                message or f"function process control exited {result.exit_code}"
            )

    @staticmethod
    def _process_state(state: ProcessStateRecord | None) -> ProcessState:
        if state is None:
            return ProcessState.STARTING
        return ProcessState(state.state.value)

    @staticmethod
    def _terminal_process_states() -> frozenset[ProcessState]:
        return frozenset(
            {
                ProcessState.SUCCEEDED,
                ProcessState.FAILED,
                ProcessState.CANCELLED,
                ProcessState.TIMED_OUT,
            }
        )

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
        if private_network:
            attachment = inspected.network_settings.networks.get(private_network)
            if attachment is None or not attachment.ip_address:
                raise WorkspaceRuntimeError(
                    "Docker runtime is not attached to the configured private network"
                )
            return WorkspaceRuntimeClient(
                f"http://{attachment.ip_address}:{runtime_port}",
                token,
                request_timeout_seconds=request_timeout_seconds,
            )
        bindings = inspected.network_settings.ports.get(f"{runtime_port}/tcp")
        if not bindings:
            raise WorkspaceRuntimeError("Docker runtime port is not published")
        return WorkspaceRuntimeClient(
            f"http://127.0.0.1:{bindings[0].host_port}",
            token,
            request_timeout_seconds=request_timeout_seconds,
        )

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
