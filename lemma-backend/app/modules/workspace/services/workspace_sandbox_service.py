"""Sessions, environment and file access over a provisioned workspace.

Sits above ``SandboxService``: that decides a sandbox exists, this decides
what a caller is allowed to do with one and hands back a session bound to
the right workspace, cwd and credentials.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from opentelemetry import trace

from sandbox_runtime.paths import WORKSPACE_ROOT
from app.core.bounded import BoundedDict
from app.core.config import settings
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from sandbox_runtime.protocol import (
    PortAccessGrant,
    PortProtocol,
    WorkloadKind,
)
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.sandbox_session import (
    SandboxWorkspaceSession,
    canonical_workspace_cwd,
    forget_python_sessions,
)
from app.modules.workspace.services.interfaces import ISandbox, IWorkspaceSession
from app.modules.workspace.services.local_sandbox_client import LocalSandboxClient
from app.modules.workspace.services.workspace_directory_ensure import (
    WorkspaceDirectoryEnsureMixin,
)
from app.modules.workspace.services.workspace_process_store import WorkspaceProcessStore
from app.modules.workspace.services.workspace_runtime_bundle import (
    WorkspaceRuntimeBundleMixin,
)
from app.modules.workspace.services.workspace_storage_generation_store import (
    WorkspaceStorageGenerationStore,
)
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.services.workspace_directory_ensure import ReadyBudget

logger = get_logger(__name__)

_storage_generation_store: WorkspaceStorageGenerationStore | None = None
_process_store: WorkspaceProcessStore | None = None

# Own tracer rather than the agent module's run_phase helper: a workspace
# session is acquired again for every single shell tool call, and the split
# between "ask the sandbox manager where the box is" and "mint the env vars" is
# only visible from inside this module. Hard-coded ``app.`` name because the
# span sanitizer keeps a span's own name only for scopes under ``app.``.
_tracer = trace.get_tracer("app.modules.workspace.tool_phases")


def get_workspace_storage_generation_store() -> WorkspaceStorageGenerationStore:
    global _storage_generation_store
    if _storage_generation_store is None:
        _storage_generation_store = WorkspaceStorageGenerationStore()
    return _storage_generation_store


def get_workspace_process_store() -> WorkspaceProcessStore:
    global _process_store
    if _process_store is None:
        _process_store = WorkspaceProcessStore()
    return _process_store


async def reset_workspace_store_state() -> None:
    """Close and reset global workspace redis stores (used by tests)."""
    global _storage_generation_store, _process_store
    if _storage_generation_store is not None:
        await _storage_generation_store.close()
        _storage_generation_store = None
    if _process_store is not None:
        await _process_store.close()
        _process_store = None


class WorkspaceSandboxService(
    WorkspaceRuntimeBundleMixin, WorkspaceDirectoryEnsureMixin
):
    """Service for user-scoped workspace sandbox lifecycle and sessions."""

    _inflight_ensures: dict[tuple[int, UUID], asyncio.Task[SandboxInfo]] = {}
    _inflight_directories: dict[
        tuple[int, UUID, str, int, str], asyncio.Task[SandboxInfo]
    ] = {}
    # Directories already created, by the same key. The singleflight above only
    # collapses concurrent callers, so every tool call paid a full sandbox round
    # trip to mkdir a directory that had existed since the first command. The
    # key carries the storage generation, so a disk reset misses while a mere
    # container recreate keeps what is still on the volume.
    # Bounded: an entry only goes when read after expiry or its user's sandbox
    # is forgotten, and keys include arbitrary paths.
    _ready_directories: BoundedDict[tuple[int, UUID, str, int, str], float] = (
        BoundedDict(4096, name="workspace.ready_directories")
    )
    _stopping: dict[tuple[int, UUID], asyncio.Event] = {}

    def __init__(
        self,
        *,
        sandbox: Optional[ISandbox] = None,
        storage_generation_store: Optional[WorkspaceStorageGenerationStore] = None,
        process_store: Optional[WorkspaceProcessStore] = None,
        manager_client: Optional[LocalSandboxClient] = None,
    ):
        self._sandbox = sandbox
        self._manager_client = manager_client
        self.storage_generation_store = (
            storage_generation_store or get_workspace_storage_generation_store()
        )
        self.process_store = process_store or get_workspace_process_store()

    @property
    def sandbox(self) -> ISandbox:
        """The sandbox backend, built on first use.

        Built lazily because constructing it resolves a provider, and the Docker
        provider refuses to exist without `WORKSPACE_RUNTIME_CREDENTIAL_KEY`.
        Several callers only want `get_env_vars`, which mints a token and never
        provisions anything — most importantly the Agent Host credential
        refresh, which is what keeps a long ACP run's tools working past the
        first hour. Building the provider eagerly made that refresh fail on any
        deployment where sandbox provisioning was unconfigured, and the failure
        was swallowed as a warning, so the run silently carried on toward a
        cliff where every `lemma_*` call would start returning 401.
        """
        if self._sandbox is None:
            self._sandbox = self._build_sandbox()
        return self._sandbox

    def _build_sandbox(self) -> ISandbox:
        from app.modules.workspace.services.sandbox_composition import LocalSandbox

        return LocalSandbox()

    async def close(self) -> None:
        # Deliberately does not go through the property: closing a service that
        # never provisioned anything must not build a provider in order to shut
        # it down again.
        if self._sandbox is None:
            return
        close = getattr(self._sandbox, "close", None)
        if close is not None:
            await close()

    @classmethod
    async def close_shared_manager_client(cls) -> None:
        """Cancel in-flight directory work at shutdown.

        The name is from when this also disposed a pooled HTTP client to the
        manager. The client is in-process now and owns no connection pool, so
        only the tasks remain.
        """
        directory_tasks = tuple(cls._inflight_directories.values())
        for task in directory_tasks:
            task.cancel()
        if directory_tasks:
            await asyncio.gather(*directory_tasks, return_exceptions=True)
        cls._inflight_directories.clear()
        cls._ready_directories.clear()

    async def _get_sandbox_info(self, user_id: UUID) -> SandboxInfo | None:
        return await self.sandbox.get_sandbox(user_id)

    async def _ensure_sandbox_info(self, user_id: UUID) -> SandboxInfo:
        return await self.sandbox.ensure_sandbox(user_id)

    @staticmethod
    def _resolve_workspace_api_url() -> str:
        if workspace_settings.workspace_callback_api_url:
            return workspace_settings.workspace_callback_api_url
        return settings.cli_api_url or settings.api_url

    async def _delete_sandbox(
        self, user_id: UUID, sandbox_info: SandboxInfo | None
    ) -> None:
        del sandbox_info
        suspend = getattr(self.sandbox, "suspend_sandbox", None)
        if suspend is not None:
            await suspend(user_id)
            return
        # Compatibility for external ISandbox implementations written before
        # non-destructive suspension became an optional capability.
        await self.sandbox.delete_sandbox(user_id)

    async def _get_or_create_sandbox_once(
        self,
        user_id: UUID,
        *,
        force_reconcile: bool,
    ) -> SandboxInfo:
        # A read is cheap and cannot consume create admission. Only issue the
        # idempotent PUT when the sandbox runtime says the sandbox is absent/not ready.
        existing = None if force_reconcile else await self._get_sandbox_info(user_id)
        if existing is not None and existing.status == "RUNNING":
            return existing
        return await self._ensure_sandbox_info(user_id)

    async def get_or_create_sandbox(
        self,
        user_id: UUID,
        *,
        force_reconcile: bool = False,
    ) -> SandboxInfo:
        """Ensure one ready sandbox per user without concurrent PUT herds."""
        key = (id(asyncio.get_running_loop()), user_id)
        while stopping := self._stopping.get(key):
            await stopping.wait()
        # There is deliberately no await between the stop-marker check and
        # singleflight insertion. On one event loop, stop cannot interleave in
        # this critical section.
        task = self._inflight_ensures.get(key)
        if task is None:
            task = create_inherited_task(
                self._get_or_create_sandbox_once(
                    user_id,
                    force_reconcile=force_reconcile,
                ),
                name=f"workspace-sandbox-ensure:{user_id}",
            )
            self._inflight_ensures[key] = task

            def clear(completed: asyncio.Task[SandboxInfo]) -> None:
                if self._inflight_ensures.get(key) is completed:
                    self._inflight_ensures.pop(key, None)

            task.add_done_callback(clear)
        return await asyncio.shield(task)

    @classmethod
    def forget_workspace(cls, user_id: UUID) -> None:
        """Drop what is remembered about a user's workspace.

        One entry point, because everything remembered here stops being true at
        the same moment: the sandbox went away.
        """
        loop_key = (id(asyncio.get_running_loop()), user_id)
        for cache_key in [
            cache_key
            for cache_key in cls._ready_directories
            if cache_key[: len(loop_key)] == loop_key
        ]:
            cls._ready_directories.pop(cache_key, None)
        forget_python_sessions(user_id)

    async def stop_sandbox(self, user_id: UUID) -> None:
        key = (id(asyncio.get_running_loop()), user_id)
        existing_stop = self._stopping.get(key)
        if existing_stop is not None:
            await existing_stop.wait()
            return

        stopped = asyncio.Event()
        self._stopping[key] = stopped
        try:
            directory_tasks = tuple(
                task
                for cache_key, task in self._inflight_directories.items()
                if cache_key[: len(key)] == key
            )
            # A stopped sandbox's directories are not ready any more, whatever
            # the epoch says: stopping is how a workspace is torn down.
            self.forget_workspace(user_id)
            for task in directory_tasks:
                task.cancel()
            if directory_tasks:
                await asyncio.gather(*directory_tasks, return_exceptions=True)
            inflight = self._inflight_ensures.get(key)
            if inflight is not None:
                try:
                    await asyncio.shield(inflight)
                except Exception:
                    # Stop still inspects and releases whatever the provider owns.
                    pass
            sandbox_info = await self._get_sandbox_info(user_id)
            await self._delete_sandbox(user_id, sandbox_info)
        finally:
            if self._stopping.get(key) is stopped:
                self._stopping.pop(key, None)
            stopped.set()

    async def create_browser_access(
        self,
        user_id: UUID,
        *,
        ttl_seconds: int,
        ensure_sandbox: bool = True,
    ) -> PortAccessGrant:
        if ensure_sandbox:
            await self.get_or_create_sandbox(user_id)
        return await self._get_manager_client().create_port_access(
            WorkloadKind.WORKSPACE,
            user_id,
            4848,
            protocol=PortProtocol.HTTP,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        )

    async def get_env_vars(
        self,
        user_id: UUID,
        pod_id: UUID | None,
        *,
        workspace_url: str | None = None,
        organization_id: UUID | None = None,
        workload_type: str | None = None,
        workload_id: UUID | None = None,
        workload_name: str | None = None,
        scope: list[str] | None = None,
        session_id: str | None = None,
    ) -> dict[str, str]:
        from app.modules.identity.contracts.delegated_tokens import (
            mint_delegated_token,
        )

        token = await mint_delegated_token(
            user_id=user_id,
            workload_type=workload_type,
            workload_id=workload_id,
            pod_id=pod_id,
            session_id=session_id or str(uuid4()),
            workload_name=workload_name,
            scope=scope,
            delegated_tokens_enabled=settings.authz_delegated_tokens_enabled,
        )
        api_url = self._resolve_workspace_api_url()
        auth_url = (
            workspace_settings.workspace_callback_auth_url
            or settings.cli_auth_frontend_url
            or settings.auth_frontend_url
        )
        host_origin = (
            workspace_settings.workspace_callback_frontend_url or settings.frontend_url
        )

        resolved_org_id = (
            str(organization_id)
            if organization_id is not None
            else await self._resolve_organization_id(pod_id)
        )
        env_vars = {
            "LEMMA_TOKEN": token,
            "LEMMA_BASE_URL": api_url,
            "LEMMA_AUTH_URL": auth_url,
            "LEMMA_HOST_ORIGIN": host_origin,
            "LEMMA_USER_ID": str(user_id),
            "LEMMA_POD_ID": str(pod_id) if pod_id is not None else None,
            "LEMMA_ORG_ID": resolved_org_id,
            "LEMMA_WORKSPACE_URL": workspace_url,
        }
        return {k: v for k, v in env_vars.items() if v is not None}

    async def _resolve_organization_id(self, pod_id: UUID | None) -> str | None:
        if pod_id is None:
            return None
        from app.modules.pod.contracts.detached_reads import (
            pod_organization_id_detached,
        )

        organization_id = await pod_organization_id_detached(pod_id)
        return str(organization_id) if organization_id else None

    async def get_session(
        self,
        user_id: UUID,
        pod_id: UUID | None,
        session_id: Optional[str] = None,
        initial_cwd: str = WORKSPACE_ROOT,
        close_on_exit: bool = True,
        workload_type: str | None = None,
        workload_id: UUID | None = None,
        workload_name: str | None = None,
        organization_id: UUID | None = None,
        scope: list[str] | None = None,
        env_vars: dict[str, str] | None = None,
        ready_timeout_seconds: float | None = None,
    ) -> IWorkspaceSession:
        resolved_cwd = canonical_workspace_cwd(initial_cwd)
        budget = ReadyBudget(ready_timeout_seconds)
        with _tracer.start_as_current_span("lemma.workspace.ensure_dir"):
            sandbox_info = await self._ensure_workspace_directory(
                user_id, resolved_cwd, budget=budget
            )
        with _tracer.start_as_current_span("lemma.workspace.runtime_bundle"):
            # The install is a shielded shared task: running out of budget
            # abandons this caller's wait and leaves it running for the next.
            await self._await_shared(
                self._ensure_runtime_bundle(user_id, sandbox_info),
                budget.remaining(),
            )
            await self._ensure_browser_proxy(
                user_id, sandbox_info, wait_seconds=budget.remaining()
            )

        if env_vars is None:
            with _tracer.start_as_current_span("lemma.workspace.env_vars"):
                # Minting the token and resolving the organization are reads
                # like any other; a stalled one must not outlast the ceiling.
                env_vars = await self._await_shared(
                    self.get_env_vars(
                        user_id,
                        pod_id,
                        workspace_url=sandbox_info.endpoint,
                        organization_id=organization_id,
                        workload_type=workload_type,
                        workload_id=workload_id,
                        workload_name=workload_name,
                        scope=scope,
                        session_id=session_id,
                    ),
                    budget.remaining(),
                )

        # Tell this session, once, if the disk it is about to use is not the one
        # it saw last. Without it an agent cannot distinguish a recreated
        # workspace from an ordinary empty directory.
        workspace_recreated = False
        if session_id and sandbox_info.storage_generation is not None:
            try:
                with _tracer.start_as_current_span(
                    "lemma.workspace.storage_generation"
                ):
                    # Best effort, and bounded like the rest: the handler
                    # below turns a timeout into "no notice".
                    workspace_recreated = await asyncio.wait_for(
                        self.storage_generation_store.observe_storage_generation(
                            session_id=session_id,
                            generation=sandbox_info.storage_generation,
                        ),
                        timeout=budget.remaining(),
                    )
            except Exception:
                # A missing notice is far better than a failed tool call.
                workspace_recreated = False

        return SandboxWorkspaceSession(
            client=self._get_manager_client(),
            sandbox_id=str(user_id),
            session_id=session_id,
            env_vars=env_vars,
            initial_cwd=resolved_cwd,
            auto_close=close_on_exit,
            owns_client=False,
            output_cursor_store=self.process_store,
            workspace_recreated=workspace_recreated,
            # Identifies the container, so a recreate invalidates the remembered
            # interpreter that died with the old one.
            allocation_epoch=sandbox_info.allocation_epoch,
        )

    def _get_manager_client(self) -> LocalSandboxClient:
        """The client the session and file operations run through.

        In-process, with the surface the sandbox HTTP client had -- which is
        why the session above it never needed to know the difference. A client
        passed to the constructor is used instead, which is how a test gives
        the service a fabric without replacing part of the service itself.
        """
        if self._manager_client is not None:
            return self._manager_client
        from app.modules.workspace.services.sandbox_composition import (
            build_local_client,
        )

        return build_local_client()
