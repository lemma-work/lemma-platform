"""Docker sandbox provider.

Speaks the Docker Engine REST API directly over the unix socket via
``docker_engine.py`` -- no docker SDK, so this costs no dependency. Pointing
the same client at a TCP endpoint with mTLS is what makes remote Docker a
transport swap rather than a new provider.

Two things differ from the adapter this replaces, and both are deliberate:

*Containers are named deterministically*, so create is idempotent and the name
carries the epoch fence. Retrying a create either creates the name or finds it
already there, which is why no create-attempt ledger or reconciler is needed.

*Volumes are adopted, never derived.* The volume holding a user's files was
named from a random token in a database that is being retired, so it is found
by label instead. A volume is only named by us when there is nothing to adopt.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import tarfile
from collections.abc import AsyncIterable, AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from uuid import UUID

import httpx

from agentbox.domain import (
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

from app.modules.workspace.domain.sandbox import SandboxKind, SandboxMount
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import (
    LABEL_EPOCH,
    LABEL_MANAGED_BY,
    LABEL_SANDBOX_ID,
    LABEL_SANDBOX_KIND,
    LEGACY_LOGICAL_ID,
    LEGACY_MANAGED_BY,
    MANAGED_BY,
    ProviderCreateAmbiguous,
    ProviderCreateSpec,
    ProviderFailed,
    ProviderGone,
    ProviderInstance,
    ProviderNotReady,
    ProviderObject,
    ProviderRejected,
    ProviderStorageKind,
    ProcessDescriptor,
)
from app.modules.workspace.providers.docker_engine import (
    DockerContainerCreateRequest,
    DockerContainerInspect,
    DockerEmptyObject,
    DockerEngineClient,
    DockerEngineError,
    DockerHostConfig,
    DockerPortBinding,
    DockerRequestAmbiguous,
    DockerVolumeCreateRequest,
)
from app.modules.workspace.providers.profiles import SandboxProfile, profile_for
from app.modules.workspace.providers.runtime_client import (
    WorkspaceRuntimeClient,
    WorkspaceRuntimeError,
    WorkspaceRuntimeFileConflict,
    WorkspaceRuntimeFileNotFound,
    WorkspaceRuntimeFileRejected,
)

_BOOTSTRAP_DIR = "/run/agentbox-bootstrap"


@dataclass(frozen=True, slots=True)
class DockerProviderConfig:
    allow_mutable_images: bool = False
    memory_bytes: int = 2 * 1024 * 1024 * 1024
    nano_cpus: int = 1_000_000_000
    function_memory_bytes: int = 2 * 1024 * 1024 * 1024
    function_nano_cpus: int = 4_000_000_000
    pids_limit: int = 512
    add_host_gateway: bool = False
    host_alias: str | None = None
    private_network: str | None = None
    max_file_transfer_bytes: int = 256 * 1024 * 1024

    def __post_init__(self) -> None:
        if min(self.memory_bytes, self.nano_cpus) < 1:
            raise ValueError("Docker memory and CPU limits must be positive")
        if self.add_host_gateway and not self.host_alias:
            raise ValueError(
                "Docker host alias is required when host-gateway injection is enabled"
            )


@dataclass(frozen=True, slots=True)
class RuntimeCredentialSigner:
    """Derives the per-container token the in-sandbox runtime will accept."""

    key: bytes

    def __post_init__(self) -> None:
        if len(self.key) < 32:
            raise ValueError("runtime credential signing key must be at least 32 bytes")

    def token(self, provider_id: str) -> str:
        digest = hmac.new(self.key, provider_id.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")


class DockerSandboxProvider:
    name = "docker"
    # A container and its volume are separate objects, so compute can be
    # replaced without touching the user's files.
    storage_kind = ProviderStorageKind.VOLUME

    def __init__(
        self,
        engine: DockerEngineClient,
        config: DockerProviderConfig,
        runtime_credentials: RuntimeCredentialSigner | None = None,
    ) -> None:
        self._engine = engine
        self._config = config
        self._runtime_credentials = runtime_credentials

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def create(self, spec: ProviderCreateSpec) -> ProviderInstance:
        profile = profile_for(spec.kind)
        image = spec.image or profile.image
        if not self._config.allow_mutable_images and "@sha256:" not in image:
            raise ProviderRejected(
                "Docker profile image must be pinned by sha256 digest"
            )

        # Idempotence: the name is derived, so a retry after a lost response
        # finds the container rather than creating a second one.
        existing = await self.inspect(spec.name, deadline_at=spec.deadline_at)
        if existing is not None:
            return existing

        labels = {
            LABEL_MANAGED_BY: MANAGED_BY,
            LABEL_SANDBOX_ID: str(spec.sandbox_id),
            LABEL_SANDBOX_KIND: spec.kind.value,
            LABEL_EPOCH: str(spec.epoch),
            "profile-name": spec.profile_name or profile.name,
            "profile-digest": spec.profile_digest or profile.digest,
        }

        binds: list[str] = []
        if spec.volume_name is not None:
            labels["workspace-storage-id"] = spec.volume_name
            binds.append(f"{spec.volume_name}:/workspace")
        binds.extend(_bind(mount) for mount in spec.mounts)

        is_function = profile.is_function
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
            binds=tuple(binds),
            port_bindings=(
                {}
                if self._config.private_network
                else {
                    f"{port}/tcp": (
                        DockerPortBinding(host_ip="127.0.0.1", host_port=""),
                    )
                    for port in profile.published_ports
                }
            ),
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
            # Function control state lives entirely in /tmp, so a read-only
            # root enforces the stateless contract instead of trusting it.
            readonly_rootfs=is_function,
            tmpfs=tmpfs,
            extra_hosts=(
                (f"{self._config.host_alias}:host-gateway",)
                if self._config.add_host_gateway
                else ()
            ),
            network_mode=self._config.private_network,
        )

        env = [
            f"AGENTBOX_MAX_FILE_TRANSFER_BYTES={self._config.max_file_transfer_bytes}"
        ]
        if is_function:
            env.append("LEMMA_FUNCTION_CACHE_ROOT=/run/lemma-function-cache")
        env.extend(f"{name}={value}" for name, value in sorted(spec.env.items()))

        request = DockerContainerCreateRequest(
            image=image,
            command=None,
            labels=labels,
            exposed_ports={
                f"{port}/tcp": DockerEmptyObject() for port in profile.published_ports
            },
            host_config=host_config,
            working_dir=profile.working_dir,
            env=tuple(env),
        )
        try:
            created = await self._engine.create_container(
                spec.name, request, deadline_at=spec.deadline_at
            )
        except DockerRequestAmbiguous as exc:
            # The name is deterministic, so recovery is to look, not to
            # reconcile: if it landed, the next create finds it.
            raise ProviderCreateAmbiguous(str(exc)) from exc
        except DockerEngineError as exc:
            # A name collision means a concurrent create won the race, which is
            # success for an idempotent operation.
            found = await self.inspect(spec.name, deadline_at=spec.deadline_at)
            if found is not None:
                return found
            raise ProviderRejected(str(exc)) from exc

        return ProviderInstance(
            provider_id=created.container_id,
            name=spec.name,
            volume_name=spec.volume_name,
            running=False,
        )

    async def inspect(
        self, name: str, *, deadline_at: datetime
    ) -> ProviderInstance | None:
        try:
            inspected = await self._engine.inspect_container(
                name, deadline_at=deadline_at
            )
        except DockerEngineError as exc:
            raise ProviderRejected(str(exc)) from exc
        if inspected is None:
            return None
        return ProviderInstance(
            provider_id=inspected.container_id,
            name=name,
            volume_name=inspected.config.labels.get("workspace-storage-id"),
            running=inspected.state.running,
        )

    async def wait_ready(
        self,
        instance: ProviderInstance,
        *,
        kind: SandboxKind,
        deadline_at: datetime,
    ) -> None:
        profile = profile_for(kind)
        try:
            await self._engine.start_container(
                instance.provider_id, deadline_at=deadline_at
            )
            inspected = await self._await_running(instance, deadline_at=deadline_at)
            if profile.is_function:
                await self._wait_function_runtime(
                    inspected, profile=profile, deadline_at=deadline_at
                )
            else:
                await self._wait_workspace_runtime(
                    inspected, profile=profile, deadline_at=deadline_at
                )
        except (ProviderNotReady, ProviderFailed):
            raise
        except (DockerEngineError, KeyError, ValueError) as exc:
            raise ProviderFailed(str(exc)) from exc

    async def _await_running(
        self, instance: ProviderInstance, *, deadline_at: datetime
    ) -> DockerContainerInspect:
        while datetime.now(timezone.utc) < deadline_at:
            inspected = await self._engine.inspect_container(
                instance.provider_id, deadline_at=deadline_at
            )
            if inspected is None:
                raise ProviderFailed("Docker container disappeared")
            if inspected.state.running:
                return inspected
            if inspected.state.status in {"dead", "exited"}:
                raise ProviderFailed(
                    "Docker container exited before readiness "
                    f"(exit={inspected.state.exit_code})"
                )
            await asyncio.sleep(0.05)
        raise ProviderNotReady("Docker container is still starting")

    async def release(
        self,
        instance: ProviderInstance,
        *,
        kind: SandboxKind,
        deadline_at: datetime,
    ) -> None:
        """Stop compute but keep the volume, so the sandbox can be resumed."""
        if kind is SandboxKind.WORKSPACE:
            await self._try_quiesce(instance, deadline_at=deadline_at)
        try:
            await self._engine.stop_container(
                instance.provider_id, deadline_at=deadline_at, grace_seconds=5
            )
        except DockerEngineError as exc:
            raise ProviderRejected(str(exc)) from exc

    async def _try_quiesce(
        self, instance: ProviderInstance, *, deadline_at: datetime
    ) -> None:
        """Best effort: drop non-portable compute state before stopping.

        Never allowed to fail the release. A workspace whose runtime cannot be
        reached is exactly the one most in need of being stopped, so an
        unreachable runtime must not leave the container running forever.
        """
        client: WorkspaceRuntimeClient | None = None
        try:
            client = await self._runtime_client(
                instance.provider_id, deadline_at=deadline_at
            )
            await client.quiesce(deadline_at=deadline_at)
        except (WorkspaceRuntimeError, DockerEngineError, ProviderGone):
            return
        finally:
            if client is not None:
                await client.close()

    async def destroy(self, name: str, *, deadline_at: datetime) -> None:
        try:
            await self._engine.delete_container(
                name, deadline_at=deadline_at, force=True
            )
        except DockerEngineError as exc:
            raise ProviderRejected(str(exc)) from exc

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    async def find_volume(
        self, *, sandbox_id: UUID, deadline_at: datetime
    ) -> str | None:
        """Locate this sandbox's volume, including one created before cutover.

        The pre-consolidation volume is labelled with the AgentBox logical id,
        which for a default workspace was the user id -- and the migration set
        the sandbox id to that same value precisely so this lookup matches.
        Losing this lookup means losing the user's files.
        """
        ours = await self._list_volumes(
            {LABEL_MANAGED_BY: MANAGED_BY, LABEL_SANDBOX_ID: str(sandbox_id)},
            deadline_at=deadline_at,
        )
        if ours:
            return ours[0]
        legacy = await self._list_volumes(
            {LABEL_MANAGED_BY: LEGACY_MANAGED_BY, LEGACY_LOGICAL_ID: str(sandbox_id)},
            deadline_at=deadline_at,
        )
        return legacy[0] if legacy else None

    async def ensure_volume(
        self,
        *,
        sandbox_id: UUID,
        name: str,
        deadline_at: datetime,
    ) -> str:
        """Create the named volume if it is not already there."""
        existing = await self._engine.inspect_volume(name, deadline_at=deadline_at)
        if existing is not None:
            return existing.name
        try:
            created = await self._engine.create_volume(
                DockerVolumeCreateRequest(
                    name=name,
                    labels={
                        LABEL_MANAGED_BY: MANAGED_BY,
                        LABEL_SANDBOX_ID: str(sandbox_id),
                    },
                ),
                deadline_at=deadline_at,
            )
        except DockerEngineError as exc:
            raise ProviderRejected(str(exc)) from exc
        return created.name

    async def destroy_volume(self, name: str, *, deadline_at: datetime) -> None:
        try:
            await self._engine.delete_volume(name, deadline_at=deadline_at)
        except DockerEngineError as exc:
            raise ProviderRejected(str(exc)) from exc

    async def _list_volumes(
        self, labels: dict[str, str], *, deadline_at: datetime
    ) -> tuple[str, ...]:
        try:
            volumes = await self._engine.list_volumes(
                labels=labels, deadline_at=deadline_at
            )
        except DockerEngineError as exc:
            raise ProviderRejected(str(exc)) from exc
        return tuple(volume.name for volume in volumes)

    # ------------------------------------------------------------------
    # Reclamation
    # ------------------------------------------------------------------

    async def list_objects(
        self, *, deadline_at: datetime
    ) -> tuple[ProviderObject, ...]:
        """Everything this provider holds that a sweep may be responsible for.

        Legacy objects are included on purpose. A container created before the
        cutover carries `managed-by=agentbox` and no epoch label; if the sweep
        did not recognise it, it would run forever with nobody to reap it.
        """
        found: list[ProviderObject] = []
        for label_set, legacy in (
            ({LABEL_MANAGED_BY: MANAGED_BY}, False),
            ({LABEL_MANAGED_BY: LEGACY_MANAGED_BY}, True),
        ):
            try:
                containers = await self._engine.list_containers(
                    labels=label_set, deadline_at=deadline_at
                )
            except DockerEngineError as exc:
                raise ProviderRejected(str(exc)) from exc
            for container in containers:
                found.append(_as_object(container, legacy=legacy))
        return tuple(found)

    async def close(self) -> None:
        await self._engine.close()

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
            stream = await client.open_file(
                path, byte_range, deadline_at=deadline_at
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
            await client.delete_file(path, recursive=recursive, deadline_at=deadline_at)
            return True

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
        from app.modules.workspace.domain.errors import (
            SandboxPathConflict,
            SandboxPathNotFound,
            SandboxRejected,
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
        except ProviderGone:
            raise
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

    def _base_url(
        self, inspected: DockerContainerInspect, *, runtime_port: int
    ) -> str:
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

    async def port_base_url(
        self, instance: ProviderInstance, *, port: int, deadline_at: datetime
    ) -> str:
        """Where a published port of this sandbox can be reached."""
        inspected = await self._engine.inspect_container(
            instance.provider_id, deadline_at=deadline_at
        )
        if inspected is None:
            raise ProviderGone(f"sandbox container {instance.provider_id} is gone")
        return self._base_url(inspected, runtime_port=port)


def _bind(mount: SandboxMount) -> str:
    suffix = ":ro" if mount.read_only else ""
    return f"{mount.host_path}:{mount.container_path}{suffix}"


def _as_object(container, *, legacy: bool) -> ProviderObject:
    labels: Mapping[str, str] = container.labels
    name = (container.names[0] if getattr(container, "names", None) else "").lstrip("/")

    parsed = naming.parse_container_name(name)
    if parsed is not None:
        sandbox_id, _, epoch = parsed
    else:
        sandbox_id, epoch = None, None
        raw_id = labels.get(LABEL_SANDBOX_ID) or labels.get(LEGACY_LOGICAL_ID)
        if raw_id:
            try:
                sandbox_id = UUID(raw_id)
            except ValueError:
                sandbox_id = None
        raw_epoch = labels.get(LABEL_EPOCH)
        if raw_epoch:
            try:
                epoch = int(raw_epoch)
            except ValueError:
                epoch = None

    return ProviderObject(
        provider_id=container.container_id,
        name=name,
        sandbox_id=sandbox_id,
        epoch=epoch,
        running=container.state.lower() == "running",
        legacy=legacy,
    )


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
