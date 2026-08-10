"""A drop-in replacement for ``LocalSandboxClient``, backed by this module.

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

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sandbox_runtime.protocol import (
    SandboxProfileRef,
    EnvironmentVariable,
    ProcessOutputSnapshot,
    ProcessState,
    StartProcessRequest,
    TerminalSize,
)

from sandbox_runtime.protocol import (
    FunctionRuntimeLease,
    PortAccessGrant,
    PortProtocol,
    SandboxKey,
    WorkloadKind,
)

from app.core.config import settings
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.sandbox import (
    SandboxHandle,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.providers.base import ProviderInstance
from app.modules.workspace.providers.profiles import FUNCTION_RUNTIME_PORT
from app.modules.workspace.services.port_access import PortAccessSigner, PortGrant
from app.modules.workspace.services.local_sandbox_files import (
    LocalSandboxFilesMixin,
)
from app.modules.workspace.services.sandbox_service import SandboxService


@dataclass(frozen=True, slots=True)
class LocalProcessRef:
    """What the session actually reads off a started process.

    Deliberately not ``sandbox_runtime.protocol.ProcessRef``: that type requires an
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
class LocalSandboxHandle:
    """What callers read off an ensured sandbox.

    `ready` is the field that matters and the one easiest to omit: the function
    route resolver branches on it, and a handle without it raises an
    AttributeError that no handler catches, so the run fails with a generic
    message and nothing in the logs. Anything this client returns in place of
    the sandbox runtime's handle has to carry the same surface.
    """

    sandbox_id: UUID
    ready: bool
    allocation_id: UUID
    allocation_epoch: int
    storage_generation: int
    retry_after_ms: int | None = None


class LocalSandboxClient(LocalSandboxFilesMixin):
    """Speaks the sandbox client surface over the local sandbox service."""

    def __init__(self, service: SandboxService) -> None:
        self._service = service

    async def __aenter__(self) -> "LocalSandboxClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Addressing
    # ------------------------------------------------------------------

    async def _ensure_row(self, logical_id: UUID, workload_kind=None) -> UUID:
        """Make sure a sandbox row exists for what the caller is addressing.

        Workspaces are backfilled per user, but a pod's function runtime is
        created on first invocation -- there is no moment before that when the
        pod is known to need one. The id is pinned to the pod id, matching the
        logical id the sandbox runtime used, so an already-running function sandbox is
        recognised rather than duplicated.
        """
        if _is_function(workload_kind):
            sandbox = await self._service.resolve(
                kind=SandboxKind.FUNCTION,
                owner_kind=SandboxOwnerKind.POD,
                owner_id=logical_id,
                sandbox_id=logical_id,
            )
            return sandbox.id
        return logical_id

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
        # Ensuring first is deliberate: a grant for a sandbox that is not
        # running would hand back a URL that 502s until something else
        # happens to start it.
        await self._service.ensure(logical_id)
        token = self._signer().sign(
            PortGrant(sandbox_id=logical_id, port=port, expires_at=expires_at)
        )
        base = (workspace_settings.port_access_url or settings.api_url).rstrip("/")
        return PortAccessGrant(
            key=SandboxKey(
                workload_kind=workload_kind or WorkloadKind.WORKSPACE,
                logical_id=logical_id,
            ),
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
        """Where a pod's function runtime can be reached, and for how long.

        The expiry must sit meaningfully *beyond* what the caller asked for.
        The endpoint cache subtracts a refresh headroom from the remaining
        lifetime to decide how long it may cache, so a lease expiring exactly
        at the caller's deadline leaves nothing to cache and is rejected --
        which under concurrency fails the invocations that joined the cache
        rather than minting their own.

        Being generous is safe here in a way it was not before. the sandbox runtime
        treated a lease as activity, so a long horizon kept idle function
        sandboxes alive; here activity is recorded when a sandbox is used, and
        the expiry only bounds caching.
        """
        sandbox_id = await self._ensure_row(logical_id, WorkloadKind.FUNCTION)
        handle = await self._service.ensure(sandbox_id)
        _, instance = await self._instance(sandbox_id)
        url = await self._provider.port_base_url(
            instance, port=FUNCTION_RUNTIME_PORT, deadline_at=deadline_at
        )
        return FunctionRuntimeLease(
            logical_id=logical_id,
            allocation_id=logical_id,
            allocation_epoch=handle.epoch,
            profile=SandboxProfileRef(
                name=workspace_settings.function_profile_name,
                digest=workspace_settings.function_profile_digest,
            ),
            url=url.rstrip("/") + "/",
            request_headers=(),
            expires_at=max(
                required_valid_until, datetime.now(timezone.utc)
            )
            + timedelta(seconds=_LEASE_HEADROOM_SECONDS),
        )

    def _signer(self) -> PortAccessSigner:
        key = workspace_settings.runtime_credential_key
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
        del profile, admission_class, deadline_at, verify_ready
        sandbox_id = await self._ensure_row(logical_id, workload_kind)
        handle = await self._service.ensure(sandbox_id)
        # `ensure` only returns once the sandbox is serving, so a handle here
        # is by definition ready.
        return LocalSandboxHandle(
            sandbox_id=handle.sandbox_id,
            ready=True,
            allocation_id=handle.sandbox_id,
            allocation_epoch=handle.epoch,
            storage_generation=handle.storage_generation,
        )

    async def inspect_sandbox(self, workload_kind, logical_id: UUID):
        """Report the sandbox without provisioning it.

        Returns the same handle shape as `ensure_sandbox` rather than the
        database row: callers branch on `.ready`, and handing back a row makes
        that an AttributeError far from here.
        """
        del workload_kind
        info = await self._service.describe(logical_id)
        if info is None:
            return None
        return LocalSandboxHandle(
            sandbox_id=logical_id,
            ready=info.status == "RUNNING",
            allocation_id=logical_id,
            allocation_epoch=info.allocation_epoch or 1,
            storage_generation=info.storage_generation or 1,
        )

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


# Beyond whatever the caller asked for, so the endpoint cache has room to
# cache after subtracting its refresh headroom (30s by default).
_LEASE_HEADROOM_SECONDS = 300


def _is_function(workload_kind) -> bool:
    return str(getattr(workload_kind, "value", workload_kind)).lower() == "function"


def _deadline(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)
