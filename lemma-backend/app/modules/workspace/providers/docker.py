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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID



from app.modules.workspace.domain.sandbox import SandboxKind, SandboxMount
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import (
    LABEL_EPOCH,
    LABEL_MANAGED_BY,
    LABEL_PROFILE_DIGEST,
    LABEL_PROFILE_NAME,
    LABEL_SANDBOX_ID,
    LABEL_SANDBOX_KIND,
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
)
from app.modules.workspace.providers.docker_ops import DockerOpsMixin
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
)



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


class DockerSandboxProvider(DockerOpsMixin):
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

    def _labels_and_binds(
        self, spec: ProviderCreateSpec, profile: SandboxProfile
    ) -> tuple[dict[str, str], list[str]]:
        """Identity to stamp on the container, and the disks to give it.

        Returned together because the workspace volume is both: the bind that
        mounts it and the label that records which volume this container
        adopted, and losing the second would strand the first.
        """

        labels = {
            LABEL_MANAGED_BY: MANAGED_BY,
            LABEL_SANDBOX_ID: str(spec.sandbox_id),
            LABEL_SANDBOX_KIND: spec.kind.value,
            LABEL_EPOCH: str(spec.epoch),
            LABEL_PROFILE_NAME: spec.profile_name or profile.name,
            LABEL_PROFILE_DIGEST: spec.profile_digest or profile.digest,
        }
        binds: list[str] = []
        if spec.volume_name is not None:
            labels["workspace-storage-id"] = spec.volume_name
            binds.append(f"{spec.volume_name}:/workspace")
        binds.extend(_bind(mount) for mount in spec.mounts)
        return labels, binds

    def _environment(
        self, spec: ProviderCreateSpec, *, is_function: bool
    ) -> list[str]:
        env = [
            f"LEMMA_MAX_FILE_TRANSFER_BYTES={self._config.max_file_transfer_bytes}"
        ]
        if is_function:
            env.append("LEMMA_FUNCTION_CACHE_ROOT=/run/lemma-function-cache")
        env.extend(f"{name}={value}" for name, value in sorted(spec.env.items()))
        return env

    async def _reusable(
        self, spec: ProviderCreateSpec, profile: SandboxProfile
    ) -> ProviderInstance | None:
        """The container this name already has, if it is still the right one.

        Two questions at once, because the answer to both is "use this or make
        a new one". *Is there one* is idempotence: the name is derived, so a
        retry after a lost response has to find the container rather than
        create a second. *Is it current* is the fence: a container keeps the
        image it was born with for as long as it lives, so reusing one built
        from an older profile would mean a fix shipped in the sandbox image
        never reaching anyone who already had a workspace.

        A stale one is destroyed here rather than left for the sweep, so the
        caller's create finds the name free. Its volume is a separate object
        and is adopted afterwards, so the user's files survive.
        """

        existing = await self.inspect(spec.name, deadline_at=spec.deadline_at)
        if existing is None:
            return None
        if existing.profile_digest == (spec.profile_digest or profile.digest):
            return existing
        await self.destroy(spec.name, deadline_at=spec.deadline_at)
        return None

    async def create(self, spec: ProviderCreateSpec) -> ProviderInstance:
        profile = profile_for(spec.kind)
        image = spec.image or profile.image
        if not self._config.allow_mutable_images and "@sha256:" not in image:
            raise ProviderRejected(
                "Docker profile image must be pinned by sha256 digest"
            )

        existing = await self._reusable(spec, profile)
        if existing is not None:
            return existing

        labels, binds = self._labels_and_binds(spec, profile)
        is_function = profile.is_function

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
            tmpfs=_tmpfs(is_function),
            extra_hosts=(
                (f"{self._config.host_alias}:host-gateway",)
                if self._config.add_host_gateway
                else ()
            ),
            network_mode=self._config.private_network,
        )

        env = self._environment(spec, is_function=is_function)

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
            profile_digest=inspected.config.labels.get(LABEL_PROFILE_DIGEST),
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
        """Locate this sandbox's volume."""
        ours = await self._list_volumes(
            {LABEL_MANAGED_BY: MANAGED_BY, LABEL_SANDBOX_ID: str(sandbox_id)},
            deadline_at=deadline_at,
        )
        return ours[0] if ours else None

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

        Volumes as well as containers. They were omitted, and since a container
        is deliberately deleted without its volume (the disk has to survive a
        restart), nothing could ever find a workspace disk again once its
        sandbox was gone -- every sandbox ever created leaked one, forever.
        """
        found: list[ProviderObject] = []
        for label_set in ({LABEL_MANAGED_BY: MANAGED_BY},):
            try:
                containers = await self._engine.list_containers(
                    labels=label_set, deadline_at=deadline_at
                )
            except DockerEngineError as exc:
                raise ProviderRejected(str(exc)) from exc
            for container in containers:
                found.append(_as_object(container))
        try:
            volumes = await self._engine.list_volumes(
                labels={LABEL_MANAGED_BY: MANAGED_BY}, deadline_at=deadline_at
            )
        except DockerEngineError as exc:
            raise ProviderRejected(str(exc)) from exc
        for volume in volumes:
            found.append(_as_volume_object(volume))
        return tuple(found)

    async def close(self) -> None:
        await self._engine.close()

def _bind(mount: SandboxMount) -> str:
    suffix = ":ro" if mount.read_only else ""
    return f"{mount.host_path}:{mount.container_path}{suffix}"


def _as_object(container) -> ProviderObject:
    labels: Mapping[str, str] = container.labels
    name = (container.names[0] if getattr(container, "names", None) else "").lstrip("/")

    parsed = naming.parse_container_name(name)
    if parsed is not None:
        sandbox_id, _, epoch = parsed
    else:
        sandbox_id, epoch = None, None
        raw_id = labels.get(LABEL_SANDBOX_ID)
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
        # Every container this provider creates carries the current labels, so
        # nothing it lists is pre-cutover.
        legacy=False,
    )


def _as_volume_object(volume) -> ProviderObject:
    """A workspace disk, as the sweep sees it.

    ``epoch`` stays None on purpose. A volume is not bound to one container
    generation -- it is the disk every generation mounts -- so it must never be
    judged by epoch, only by whether its sandbox still exists and whether its
    storage generation is current.
    """
    labels: Mapping[str, str] = volume.labels or {}
    sandbox_id: UUID | None = None
    storage_generation: int | None = None

    parsed = naming.parse_volume_name(volume.name)
    if parsed is not None:
        sandbox_id, storage_generation = parsed
    else:
        # Pre-cutover volumes embed a random token in the name but still carry
        # the logical id as a label. They are identifiable, but their generation
        # is unknowable, which leaves them reclaimable only when their sandbox
        # is gone.
        raw_id = labels.get(LABEL_SANDBOX_ID)
        if raw_id:
            try:
                sandbox_id = UUID(raw_id)
            except ValueError:
                sandbox_id = None

    return ProviderObject(
        provider_id=volume.name,
        name=volume.name,
        sandbox_id=sandbox_id,
        epoch=None,
        running=False,
        kind="volume",
        storage_generation=storage_generation,
    )


def _tmpfs(is_function: bool) -> dict[str, str]:
    """Ephemeral mounts, which only a function sandbox has.

    Function control state lives entirely in tmpfs so the root filesystem can be
    read-only. Native wheels in a verified artifact must mmap executable
    segments, so general /tmp stays noexec and one private executable mount is
    provided for the content-addressed cache alone.
    """

    if not is_function:
        return {}
    return {
        "/tmp": "rw,noexec,nosuid,size=512m",
        "/run/lemma-function-cache": (
            "rw,exec,nosuid,nodev,size=512m,mode=0700,uid=10001,gid=10001"
        ),
    }
