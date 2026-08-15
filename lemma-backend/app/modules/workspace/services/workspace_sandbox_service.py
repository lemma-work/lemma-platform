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

from app.core.config import settings
from app.core.request_context import create_inherited_task
from sandbox_runtime.protocol import (
    PortAccessGrant,
    PortProtocol,
    WorkloadKind,
)
from app.modules.workspace.contracts import SandboxInfo
from sandbox_runtime.errors import SandboxUnavailable
from app.modules.workspace.sandbox_session import (
    SandboxWorkspaceSession,
    canonical_workspace_cwd,
)
from app.modules.workspace.services.interfaces import ISandbox, IWorkspaceSession
from app.modules.workspace.services.local_sandbox_client import LocalSandboxClient
from app.modules.workspace.services.workspace_process_store import WorkspaceProcessStore
from app.modules.workspace.services.workspace_storage_generation_store import (
    WorkspaceStorageGenerationStore,
)

_storage_generation_store: WorkspaceStorageGenerationStore | None = None
_process_store: WorkspaceProcessStore | None = None
_SANDBOX_MANAGER_HTTP_TIMEOUT_SECONDS = 300.0

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


class WorkspaceSandboxService:
    """Service for user-scoped workspace sandbox lifecycle and sessions."""

    _inflight_ensures: dict[tuple[int, UUID], asyncio.Task[SandboxInfo]] = {}
    _inflight_directories: dict[
        tuple[int, UUID, str, int, str], asyncio.Task[SandboxInfo]
    ] = {}
    _stopping: dict[tuple[int, UUID], asyncio.Event] = {}

    def __init__(
        self,
        *,
        sandbox: Optional[ISandbox] = None,
        storage_generation_store: Optional[WorkspaceStorageGenerationStore] = None,
        process_store: Optional[WorkspaceProcessStore] = None,
    ):
        self._sandbox = sandbox
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

    async def _get_sandbox_info(self, user_id: UUID) -> SandboxInfo | None:
        return await self.sandbox.get_sandbox(user_id)

    async def _ensure_sandbox_info(self, user_id: UUID) -> SandboxInfo:
        return await self.sandbox.ensure_sandbox(user_id)

    @staticmethod
    def _resolve_workspace_api_url() -> str:
        if settings.workspace_callback_api_url:
            return settings.workspace_callback_api_url
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
        from app.composition.workspace_identity import mint_workspace_token

        token = await mint_workspace_token(
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
            settings.workspace_callback_auth_url
            or settings.cli_auth_frontend_url
            or settings.auth_frontend_url
        )
        host_origin = (
            settings.workspace_callback_frontend_url or settings.frontend_url
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
        from app.composition.workspace_identity import (
            resolve_workspace_organization_id,
        )

        return await resolve_workspace_organization_id(pod_id)

    async def get_session(
        self,
        user_id: UUID,
        pod_id: UUID | None,
        session_id: Optional[str] = None,
        initial_cwd: str = "/workspace",
        close_on_exit: bool = True,
        workload_type: str | None = None,
        workload_id: UUID | None = None,
        workload_name: str | None = None,
        organization_id: UUID | None = None,
        scope: list[str] | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> IWorkspaceSession:
        resolved_cwd = canonical_workspace_cwd(initial_cwd)
        with _tracer.start_as_current_span("lemma.workspace.ensure_dir"):
            sandbox_info = await self._ensure_workspace_directory(
                user_id,
                resolved_cwd,
            )

        if env_vars is None:
            with _tracer.start_as_current_span("lemma.workspace.env_vars"):
                env_vars = await self.get_env_vars(
                    user_id,
                    pod_id,
                    workspace_url=sandbox_info.endpoint,
                    organization_id=organization_id,
                    workload_type=workload_type,
                    workload_id=workload_id,
                    workload_name=workload_name,
                    scope=scope,
                    session_id=session_id,
                )

        # Tell this session, once, if the disk it is about to use is not the one
        # it saw last. Without it an agent cannot distinguish a recreated
        # workspace from an ordinary empty directory.
        workspace_recreated = False
        if session_id and sandbox_info.storage_generation is not None:
            try:
                with _tracer.start_as_current_span("lemma.workspace.storage_generation"):
                    workspace_recreated = (
                        await self.storage_generation_store.observe_storage_generation(
                            session_id=session_id,
                            generation=sandbox_info.storage_generation,
                        )
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
        )

    async def _ensure_workspace_directory(
        self,
        user_id: UUID,
        path: str,
    ) -> SandboxInfo:
        deadline_at = datetime.now(timezone.utc) + timedelta(
            seconds=_SANDBOX_MANAGER_HTTP_TIMEOUT_SECONDS
        )
        sandbox_info = await self.get_or_create_sandbox(user_id)
        cache_key = self._directory_cache_key(user_id, path, sandbox_info)
        if cache_key is None:
            return await self._create_workspace_directory_until_ready(
                user_id,
                path,
                sandbox_info=sandbox_info,
                deadline_at=deadline_at,
            )

        task = self._inflight_directories.get(cache_key)
        if task is None:
            task = create_inherited_task(
                self._create_workspace_directory_until_ready(
                    user_id,
                    path,
                    sandbox_info=sandbox_info,
                    deadline_at=deadline_at,
                ),
                name=f"workspace-directory-ensure:{user_id}:{path}",
            )
            self._inflight_directories[cache_key] = task

            def clear(completed: asyncio.Task[SandboxInfo]) -> None:
                if self._inflight_directories.get(cache_key) is completed:
                    self._inflight_directories.pop(cache_key, None)

            task.add_done_callback(clear)

        return await asyncio.shield(task)

    async def _create_workspace_directory_until_ready(
        self,
        user_id: UUID,
        path: str,
        *,
        sandbox_info: SandboxInfo,
        deadline_at: datetime,
    ) -> SandboxInfo:
        force_reconcile = False
        while datetime.now(timezone.utc) < deadline_at:
            if force_reconcile:
                sandbox_info = await self.get_or_create_sandbox(
                    user_id,
                    force_reconcile=True,
                )
            try:
                await self._get_manager_client().create_directory(
                    user_id,
                    path,
                    deadline_at=deadline_at,
                )
            except SandboxUnavailable as exc:
                remaining = (
                    deadline_at - datetime.now(timezone.utc)
                ).total_seconds()
                if remaining <= 0:
                    break
                delay = max(0.05, (exc.retry_after_ms or 250) / 1000)
                await asyncio.sleep(min(delay, remaining))
                force_reconcile = True
                continue
            return sandbox_info
        raise TimeoutError(
            f"workspace sandbox {user_id} did not become usable"
        )

    def _directory_cache_key(
        self,
        user_id: UUID,
        path: str,
        sandbox_info: SandboxInfo,
    ) -> tuple[int, UUID, str, int, str] | None:
        if (
            sandbox_info.allocation_id is None
            or sandbox_info.allocation_epoch is None
        ):
            return None
        # The leading (loop id, user id) must stay a prefix of the ensure key:
        # stop_sandbox cancels directory tasks by matching that prefix.
        return (
            id(asyncio.get_running_loop()),
            user_id,
            sandbox_info.allocation_id,
            sandbox_info.allocation_epoch,
            path,
        )

    def _get_manager_client(self) -> LocalSandboxClient:
        """The client the session and file operations run through.

        In-process, with the surface the sandbox HTTP client had -- which is
        why the session above it never needed to know the difference.
        """
        from app.modules.workspace.services.sandbox_composition import (
            build_local_client,
        )

        return build_local_client()
