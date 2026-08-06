"""Sandbox lifecycle: turning a durable row into running, addressable compute.

The whole state machine is small enough to read in one sitting, which is the
point. Deterministic naming means a create is idempotent, so there is no
attempt ledger to reconcile and no ambiguity to repair -- the recovery for
"did that create land?" is to look at the name.

Two rules carry most of the correctness:

*Recreating always bumps the epoch.* Even when the old container is already
gone and its name is free, reusing it would let a handle held across the
recreate resolve to the replacement, which is exactly the silent-wrong-target
write the fence exists to prevent.

*A volume is adopted before one is created.* The disk holding a user's files
was named from a token this schema never knew, so it is found by label. Only
when there is genuinely nothing to adopt is a name minted -- and that is also
the moment the storage generation moves, because it is the moment the user's
files are actually gone.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.config import settings
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.workspace.domain.errors import (
    SandboxNotFound,
    SandboxNotReady,
    SandboxRejected,
    SandboxUnavailable,
)
from app.modules.workspace.domain.sandbox import (
    DEFAULT_SLUG,
    Sandbox,
    SandboxDesiredState,
    SandboxHandle,
    SandboxKind,
    SandboxOwnerKind,
    capabilities_for,
)
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import (
    ProviderCreateAmbiguous,
    ProviderCreateSpec,
    ProviderFailed,
    ProviderInstance,
    ProviderNotReady,
    ProviderRejected,
    ProviderStorageKind,
)
from app.modules.workspace.providers.profiles import profile_for

logger = get_logger(__name__)

_ENSURE_TIMEOUT_SECONDS = 300.0


class SandboxService:
    """Owns the sandbox state machine. One instance per unit-of-work factory."""

    # Keyed by (event loop, sandbox id). A herd of tool calls arriving together
    # must produce one provisioning attempt, not one per caller.
    _inflight: dict[tuple[int, UUID], asyncio.Task[SandboxHandle]] = {}

    def __init__(self, *, provider, uow_factory) -> None:
        self._provider = provider
        self._uow_factory = uow_factory

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    async def resolve(
        self,
        *,
        kind: SandboxKind,
        owner_kind: SandboxOwnerKind,
        owner_id: UUID,
        slug: str = DEFAULT_SLUG,
        sandbox_id: UUID | None = None,
    ) -> Sandbox:
        """Get or create the row for a sandbox.

        ``sandbox_id`` defaults to the owner id, which is what keeps a default
        workspace addressable by the label its pre-cutover volume carries.
        """
        async with self._uow_factory() as uow:
            repository = SandboxRepository(uow)
            sandbox = await repository.ensure_row(
                sandbox_id=sandbox_id or owner_id,
                kind=kind,
                owner_kind=owner_kind,
                owner_id=owner_id,
                slug=slug,
            )
            await uow.commit()
            return sandbox

    async def get(self, sandbox_id: UUID) -> Sandbox | None:
        async with self._uow_factory() as uow:
            return await SandboxRepository(uow).get(sandbox_id)

    async def describe(self, sandbox_id: UUID):
        """Current state without provisioning anything.

        Returns the legacy ``SandboxInfo`` shape so callers that only want to
        know whether an ensure is needed do not start a container by asking.
        """
        from app.modules.workspace.contracts import SandboxInfo

        async with self._uow_factory() as uow:
            repository = SandboxRepository(uow)
            sandbox = await repository.get(sandbox_id)
            if sandbox is None:
                return None
            instance = await repository.current_instance(sandbox_id)

        running = False
        if instance is not None and instance.provider_id:
            found = await self._provider.inspect(
                instance.provider_id,
                deadline_at=datetime.now(timezone.utc) + timedelta(seconds=15),
            )
            running = found is not None and found.running

        return SandboxInfo(
            sandbox_id=str(sandbox.id),
            name=instance.provider_id if instance else str(sandbox.id),
            namespace=None,
            status="RUNNING" if running else "STOPPED",
            image="",
            created_at=None,
            endpoint=f"sandbox://{sandbox.id}",
            allocation_id=str(sandbox.id),
            allocation_epoch=sandbox.epoch,
            storage_generation=sandbox.storage_generation,
        )

    # ------------------------------------------------------------------
    # Ensure
    # ------------------------------------------------------------------

    async def ensure(self, sandbox_id: UUID) -> SandboxHandle:
        """Return a ready sandbox, provisioning it if necessary."""
        key = (id(asyncio.get_running_loop()), sandbox_id)
        task = self._inflight.get(key)
        if task is None:
            task = create_inherited_task(
                self._ensure_once(sandbox_id), name=f"sandbox-ensure:{sandbox_id}"
            )
            self._inflight[key] = task

            def clear(done: asyncio.Task[SandboxHandle]) -> None:
                if self._inflight.get(key) is done:
                    self._inflight.pop(key, None)

            task.add_done_callback(clear)
        return await asyncio.shield(task)

    async def _ensure_once(self, sandbox_id: UUID) -> SandboxHandle:
        """Provision, waiting out transient provider unavailability.

        Retry lives here rather than in a provider because it is a question
        about the caller's deadline, and the service is the only layer that
        knows it. A cloud fabric under load answers "rate limited, try in two
        seconds"; the right response is to wait and try, not to fail a user's
        tool call. Definitive refusals are not retried at all.
        """
        deadline_at = datetime.now(timezone.utc) + timedelta(
            seconds=_ENSURE_TIMEOUT_SECONDS
        )
        attempt = 0
        while True:
            try:
                return await self._attempt_ensure(sandbox_id, deadline_at=deadline_at)
            except SandboxUnavailable as exc:
                remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
                if remaining <= 0:
                    raise
                # The provider's hint is a floor, not the whole answer: backing
                # off further stops a herd of waiting callers from retrying in
                # lockstep and re-triggering the same limit.
                hint = (exc.retry_after_ms or 500) / 1000
                delay = min(remaining, max(hint, min(8.0, 0.5 * (2**attempt))))
                logger.info(
                    "workspace.sandbox_service.ensure_retrying",
                    sandbox_id=str(sandbox_id),
                    attempt=attempt,
                )
                await asyncio.sleep(delay)
                attempt += 1

    async def _attempt_ensure(
        self, sandbox_id: UUID, *, deadline_at: datetime
    ) -> SandboxHandle:
        async with self._uow_factory() as uow:
            repository = SandboxRepository(uow)
            sandbox = await repository.get(sandbox_id)
            if sandbox is None:
                raise SandboxNotFound(f"sandbox {sandbox_id} does not exist")
            instance = await repository.current_instance(sandbox_id)

        # A container that is already there is the common case, and answering
        # it costs one inspect rather than a provisioning round trip.
        if instance is not None and instance.provider_id:
            existing = await self._provider.inspect(
                naming.container_name(sandbox_id, sandbox.kind, sandbox.epoch),
                deadline_at=deadline_at,
            )
            if existing is not None:
                if not existing.running:
                    await self._start(sandbox, existing, deadline_at=deadline_at)
                await self._touch(sandbox_id)
                return self._handle(sandbox, existing)

        return await self._provision(sandbox, deadline_at=deadline_at)

    async def _provision(
        self, sandbox: Sandbox, *, deadline_at: datetime
    ) -> SandboxHandle:
        volume_name, storage_generation = await self._resolve_volume(
            sandbox, deadline_at=deadline_at
        )

        # Always a new epoch. Reusing a freed name would let a handle held
        # across the recreate address the replacement.
        async with self._uow_factory() as uow:
            repository = SandboxRepository(uow)
            current = await repository.current_instance(sandbox.id)
            epoch = (
                await repository.bump_epoch(sandbox.id)
                if current is not None
                else sandbox.epoch
            )
            profile = profile_for(sandbox.kind)
            if not sandbox.profile_digest:
                # Backfilled rows carry no profile; the first ensure adopts
                # whatever is configured rather than the migration freezing it.
                await repository.set_profile(
                    sandbox.id, name=profile.name, digest=profile.digest
                )
            name = naming.container_name(sandbox.id, sandbox.kind, epoch)
            instance = await repository.begin_instance(
                sandbox_id=sandbox.id,
                provider=self._provider.name,
                provider_id=name,
                provider_volume_id=volume_name,
                epoch=epoch,
            )
            await uow.commit()

        spec = ProviderCreateSpec(
            sandbox_id=sandbox.id,
            kind=sandbox.kind,
            epoch=epoch,
            name=name,
            image=profile.image,
            profile_name=sandbox.profile_name or profile.name,
            profile_digest=sandbox.profile_digest or profile.digest,
            deadline_at=deadline_at,
            volume_name=volume_name,
            mounts=sandbox.mounts,
        )
        try:
            created = await self._provider.create(spec)
            await self._provider.wait_ready(
                created, kind=sandbox.kind, deadline_at=deadline_at
            )
        except ProviderNotReady as exc:
            await self._fail(instance.id, str(exc))
            raise SandboxNotReady(str(exc), retry_after_ms=exc.retry_after_ms) from exc
        except ProviderCreateAmbiguous as exc:
            # The name is deterministic, so the next ensure resolves this by
            # looking rather than by reconciling.
            await self._fail(instance.id, str(exc))
            raise SandboxUnavailable(str(exc), retry_after_ms=500) from exc
        except (ProviderRejected, ProviderFailed) as exc:
            await self._fail(instance.id, str(exc))
            raise SandboxRejected(str(exc)) from exc

        # A sandbox-native provider only learns whether the disk survived by
        # trying to adopt it, so the generation is settled here rather than
        # before the create.
        if created.storage_adopted is False and sandbox.provider_volume_id:
            async with self._uow_factory() as uow:
                repository = SandboxRepository(uow)
                storage_generation = await repository.bump_storage_generation(
                    sandbox.id
                )
                await uow.commit()
            logger.info(
                "workspace.sandbox_service.workspace_storage_recreated",
                sandbox_id=str(sandbox.id),
            )

        async with self._uow_factory() as uow:
            repository = SandboxRepository(uow)
            await repository.mark_instance_ready(instance.id)
            if volume_name is not None:
                await repository.set_provider_volume(sandbox.id, volume_name)
            elif created.storage_adopted is not None:
                # Records that this sandbox has durable storage at all, so a
                # later loss is distinguishable from a first provision.
                await repository.set_provider_volume(sandbox.id, created.provider_id)
            await repository.touch(sandbox.id)
            await repository.set_desired_state(
                sandbox.id, SandboxDesiredState.PRESENT
            )
            await uow.commit()

        return self._handle(
            sandbox,
            created,
            epoch=epoch,
            storage_generation=storage_generation,
        )

    async def _resolve_volume(
        self, sandbox: Sandbox, *, deadline_at: datetime
    ) -> tuple[str | None, int]:
        """Adopt the sandbox's disk, or mint one and record that it is new.

        A function sandbox has no durable disk at all: it runs an immutable
        artifact refetched from the gateway, so a wiped function sandbox has
        lost nothing and needs no volume.
        """
        if sandbox.kind is SandboxKind.FUNCTION:
            return None, sandbox.storage_generation

        if (
            getattr(self._provider, "storage_kind", ProviderStorageKind.VOLUME)
            is ProviderStorageKind.SANDBOX_NATIVE
        ):
            # The provider's sandbox *is* the disk, so there is no separate
            # volume to manage and adoption happens inside create. The
            # generation is settled afterwards, from whether it adopted.
            return None, sandbox.storage_generation

        adopted = await self._provider.find_volume(
            sandbox_id=sandbox.id, deadline_at=deadline_at
        )
        if adopted is not None:
            return adopted, sandbox.storage_generation

        generation = sandbox.storage_generation
        if sandbox.provider_volume_id is not None:
            # We had a disk and it is not there any more. That is the one event
            # an agent must be told about, or it reads an empty directory as
            # "nothing was ever here".
            async with self._uow_factory() as uow:
                repository = SandboxRepository(uow)
                generation = await repository.bump_storage_generation(sandbox.id)
                await uow.commit()
            logger.info(
                "workspace.sandbox_service.workspace_storage_recreated",
                sandbox_id=str(sandbox.id),
            )

        name = naming.volume_name(sandbox.id, generation)
        created = await self._provider.ensure_volume(
            sandbox_id=sandbox.id, name=name, deadline_at=deadline_at
        )
        return created, generation

    async def _start(
        self, sandbox: Sandbox, instance: ProviderInstance, *, deadline_at: datetime
    ) -> None:
        try:
            await self._provider.wait_ready(
                instance, kind=sandbox.kind, deadline_at=deadline_at
            )
        except ProviderNotReady as exc:
            raise SandboxNotReady(str(exc), retry_after_ms=exc.retry_after_ms) from exc
        except (ProviderRejected, ProviderFailed) as exc:
            raise SandboxRejected(str(exc)) from exc

    # ------------------------------------------------------------------
    # Release and destroy
    # ------------------------------------------------------------------

    async def release(self, sandbox_id: UUID) -> None:
        """Stop compute, keep the disk. The next ensure resumes the sandbox."""
        deadline_at = datetime.now(timezone.utc) + timedelta(seconds=60)
        async with self._uow_factory() as uow:
            repository = SandboxRepository(uow)
            sandbox = await repository.get(sandbox_id)
            instance = await repository.current_instance(sandbox_id)
        if sandbox is None or instance is None or not instance.provider_id:
            return

        found = await self._provider.inspect(
            instance.provider_id, deadline_at=deadline_at
        )
        if found is not None:
            await self._provider.release(
                found, kind=sandbox.kind, deadline_at=deadline_at
            )

        async with self._uow_factory() as uow:
            repository = SandboxRepository(uow)
            await repository.mark_instance_released(instance.id)
            await repository.set_desired_state(
                sandbox_id, SandboxDesiredState.RELEASED
            )
            await uow.commit()

    async def destroy(self, sandbox_id: UUID, *, delete_storage: bool = False) -> None:
        deadline_at = datetime.now(timezone.utc) + timedelta(seconds=60)
        async with self._uow_factory() as uow:
            repository = SandboxRepository(uow)
            sandbox = await repository.get(sandbox_id)
            instance = await repository.current_instance(sandbox_id)
        if sandbox is None:
            return

        if instance is not None and instance.provider_id:
            await self._provider.destroy(
                instance.provider_id, deadline_at=deadline_at
            )
        if delete_storage and sandbox.provider_volume_id:
            await self._provider.destroy_volume(
                sandbox.provider_volume_id, deadline_at=deadline_at
            )

        async with self._uow_factory() as uow:
            repository = SandboxRepository(uow)
            if instance is not None:
                await repository.mark_instance_destroyed(instance.id)
            await repository.set_desired_state(sandbox_id, SandboxDesiredState.DELETED)
            await uow.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _touch(self, sandbox_id: UUID) -> None:
        async with self._uow_factory() as uow:
            await SandboxRepository(uow).touch(sandbox_id)
            await uow.commit()

    async def _fail(self, instance_id: UUID, error: str) -> None:
        async with self._uow_factory() as uow:
            await SandboxRepository(uow).mark_instance_error(instance_id, error)
            await uow.commit()

    def _handle(
        self,
        sandbox: Sandbox,
        instance: ProviderInstance,
        *,
        epoch: int | None = None,
        storage_generation: int | None = None,
    ) -> SandboxHandle:
        return SandboxHandle(
            sandbox_id=sandbox.id,
            kind=sandbox.kind,
            epoch=epoch if epoch is not None else sandbox.epoch,
            provider=self._provider.name,
            provider_id=instance.provider_id,
            capabilities=capabilities_for(sandbox.kind),
            storage_generation=(
                storage_generation
                if storage_generation is not None
                else sandbox.storage_generation
            ),
        )

    async def close(self) -> None:
        await self._provider.close()


def build_provider(name: str | None = None):
    """Construct the configured sandbox provider.

    The choice is a config value rather than a code path so a deployment can
    move between fabrics without a different build, and so exactly one place
    has to be read to know which one is live.
    """
    chosen = name or settings.workspace_provider
    if chosen == "e2b":
        return _build_e2b_provider()
    if chosen == "lemma_local":
        return _build_lemma_local_provider()
    if chosen == "docker":
        return _build_docker_provider()
    raise RuntimeError(f"unsupported workspace provider: {chosen}")


def _build_lemma_local_provider():
    from app.modules.workspace.providers.docker import RuntimeCredentialSigner
    from app.modules.workspace.providers.lemma_local import (
        LemmaLocalProviderConfig,
        LemmaLocalSandboxProvider,
    )

    executable = settings.workspace_local_runtime_cli
    if not executable:
        raise RuntimeError(
            "WORKSPACE_LOCAL_RUNTIME_CLI is required when workspace_provider "
            "is lemma_local"
        )
    key = settings.workspace_runtime_credential_key
    if not key:
        raise RuntimeError(
            "WORKSPACE_RUNTIME_CREDENTIAL_KEY is required to provision sandboxes"
        )
    return LemmaLocalSandboxProvider(
        LemmaLocalProviderConfig(
            executable=executable,
            callback_required=settings.workspace_local_callback_required,
            callback_url=settings.workspace_local_callback_url,
        ),
        RuntimeCredentialSigner(key=key.encode()),
    )


def _build_docker_provider():
    from app.modules.workspace.providers.docker import (
        DockerProviderConfig,
        DockerSandboxProvider,
        RuntimeCredentialSigner,
    )
    from app.modules.workspace.providers.docker_engine import DockerEngineClient

    key = settings.workspace_runtime_credential_key
    if not key:
        raise RuntimeError(
            "WORKSPACE_RUNTIME_CREDENTIAL_KEY is required to provision sandboxes"
        )
    return DockerSandboxProvider(
        DockerEngineClient(socket_path=settings.agentbox_docker_socket_path),
        DockerProviderConfig(
            allow_mutable_images=settings.agentbox_docker_allow_mutable_images,
            # Without the host gateway a sandbox cannot reach the backend, so
            # a function never fetches its artifact and a workspace never
            # calls back -- both fail well after provisioning looks healthy.
            add_host_gateway=settings.agentbox_add_host_gateway,
            host_alias=settings.agentbox_host_alias,
            private_network=settings.agentbox_docker_private_network,
        ),
        RuntimeCredentialSigner(key=key.encode()),
    )


def _build_e2b_provider():
    from app.core.config import reveal_secret
    from app.modules.workspace.providers.e2b import (
        E2BProviderConfig,
        E2BSandboxProvider,
    )

    api_key = reveal_secret(settings.e2b_api_key)
    if not api_key:
        raise RuntimeError("E2B_API_KEY is required when workspace_provider is e2b")
    return E2BSandboxProvider(
        E2BProviderConfig(
            api_key=api_key,
            workspace_template=settings.e2b_workspace_template,
            function_template=settings.e2b_function_template,
            domain=settings.e2b_domain,
        )
    )
