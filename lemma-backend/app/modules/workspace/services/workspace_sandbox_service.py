"""Workspace sandbox service for AgentBox and local Docker runtimes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.request_context import correlation_headers
from agentbox_client import (
    AgentBoxApiError,
    AgentBoxClient,
    PortAccessGrant,
    PortProtocol,
    RetryDisposition,
    WorkloadKind,
)
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.agentbox_session import (
    AgentBoxWorkspaceSession,
    canonical_workspace_cwd,
)
from app.modules.workspace.services.agentbox_manager import (
    AgentBoxSandbox,
    agentbox_sandbox_id,
)
from app.modules.workspace.services.interfaces import ISandbox, IWorkspaceSession
from app.modules.workspace.services.workspace_activity_store import (
    WorkspaceActivityStore,
)
from app.modules.workspace.services.workspace_state_store import WorkspaceStateStore
_activity_store: WorkspaceActivityStore | None = None
_state_store: WorkspaceStateStore | None = None
_SANDBOX_MANAGER_HTTP_TIMEOUT_SECONDS = 300.0


def get_workspace_activity_store() -> WorkspaceActivityStore:
    global _activity_store
    if _activity_store is None:
        _activity_store = WorkspaceActivityStore()
    return _activity_store


def get_workspace_state_store() -> WorkspaceStateStore:
    global _state_store
    if _state_store is None:
        _state_store = WorkspaceStateStore()
    return _state_store


async def reset_workspace_store_state() -> None:
    """Close and reset global workspace redis stores (used by tests)."""
    global _activity_store, _state_store
    if _activity_store is not None:
        await _activity_store.close()
        _activity_store = None
    if _state_store is not None:
        await _state_store.close()
        _state_store = None


class WorkspaceSandboxService:
    """Service for user-scoped workspace sandbox lifecycle and sessions."""

    # Process-shared manager client, keyed by (base_url, api_key). Reused across
    # tool calls so each call doesn't open a fresh httpx connection pool.
    _shared_manager_client: "tuple[tuple[str, str, int], AgentBoxClient] | None" = None

    def __init__(
        self,
        *,
        runtime: Optional[str] = None,
        sandbox: Optional[ISandbox] = None,
        container_manager: Optional[ISandbox] = None,
        activity_store: Optional[WorkspaceActivityStore] = None,
        state_store: Optional[WorkspaceStateStore] = None,
    ):
        self.runtime = runtime or self._resolve_runtime()
        self.sandbox = sandbox or container_manager or self._build_sandbox()
        self.activity_store = activity_store or get_workspace_activity_store()
        self.state_store = state_store or get_workspace_state_store()

    @staticmethod
    def _resolve_runtime() -> str:
        return "agentbox"

    def _build_sandbox(self) -> ISandbox:
        return AgentBoxSandbox()

    async def close(self) -> None:
        close = getattr(self.sandbox, "close", None)
        if close is not None:
            await close()

    @classmethod
    async def close_shared_manager_client(cls) -> None:
        cached = cls._shared_manager_client
        cls._shared_manager_client = None
        if cached is not None:
            await cached[1].close()

    async def _get_sandbox_info(self, user_id: UUID) -> SandboxInfo | None:
        return await self.sandbox.get_sandbox(user_id)

    async def _ensure_sandbox_info(self, user_id: UUID) -> SandboxInfo:
        return await self.sandbox.ensure_sandbox(
            user_id,
            env=self._get_sandbox_app_env(),
        )

    def _get_sandbox_app_env(self) -> dict[str, str]:
        return {
            "LEMMA_BASE_URL": self._resolve_workspace_api_url(),
        }

    def _resolve_workspace_api_url(self) -> str:
        return self.resolve_workspace_api_url_for_runtime(self.runtime)

    @staticmethod
    def resolve_workspace_api_url_for_runtime(runtime: str) -> str:
        del runtime
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

    async def _touch_workspace_activity(
        self,
        *,
        user_id: UUID,
        pod_id: Optional[UUID] = None,
        session_id: Optional[str] = None,
        sandbox_info: Optional[SandboxInfo] = None,
    ) -> None:
        await self.activity_store.mark_active(
            runtime=self.runtime,
            user_id=user_id,
            pod_id=pod_id,
            session_id=session_id,
            container_name=sandbox_info.name if sandbox_info else None,
            namespace=sandbox_info.namespace if sandbox_info else None,
            workspace_url=sandbox_info.endpoint if sandbox_info else None,
        )

    async def get_or_create_sandbox(self, user_id: UUID) -> SandboxInfo:
        """Ensure a ready sandbox; AgentBox owns allocation concurrency."""
        await self.state_store.mark_creating(runtime=self.runtime, user_id=user_id)
        try:
            sandbox_info = await self._ensure_sandbox_info(user_id)
        except Exception as exc:
            await self.state_store.mark_error(
                runtime=self.runtime,
                user_id=user_id,
                error=str(exc),
            )
            raise
        await self.state_store.mark_running(
            runtime=self.runtime,
            user_id=user_id,
            pod_name=None,
            container_name=sandbox_info.sandbox_id,
            namespace=None,
            workspace_url=sandbox_info.endpoint,
        )
        await self._touch_workspace_activity(
            user_id=user_id,
            sandbox_info=sandbox_info,
        )
        return sandbox_info

    async def stop_sandbox(self, user_id: UUID) -> None:
        sandbox_info = await self._get_sandbox_info(user_id)
        await self._delete_sandbox(user_id, sandbox_info)
        await self.activity_store.remove(runtime=self.runtime, user_id=user_id)
        await self.state_store.mark_stopped(runtime=self.runtime, user_id=user_id)

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
            agentbox_sandbox_id(user_id),
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
        sandbox_info = await self._ensure_workspace_directory(
            user_id,
            resolved_cwd,
        )

        if env_vars is None:
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

        async def _activity_callback(current_session_id: Optional[str]) -> None:
            await self._touch_workspace_activity(
                user_id=user_id,
                pod_id=pod_id,
                session_id=current_session_id or session_id,
                sandbox_info=sandbox_info,
            )

        return AgentBoxWorkspaceSession(
            client=self._get_manager_client(),
            sandbox_id=str(agentbox_sandbox_id(user_id)),
            session_id=session_id,
            env_vars=env_vars,
            initial_cwd=resolved_cwd,
            auto_close=close_on_exit,
            activity_callback=_activity_callback,
            owns_client=False,
        )

    async def _ensure_workspace_directory(
        self,
        user_id: UUID,
        path: str,
    ) -> SandboxInfo:
        deadline_at = datetime.now(timezone.utc) + timedelta(
            seconds=_SANDBOX_MANAGER_HTTP_TIMEOUT_SECONDS
        )
        while datetime.now(timezone.utc) < deadline_at:
            sandbox_info = await self.get_or_create_sandbox(user_id)
            try:
                await self._get_manager_client().create_directory(
                    agentbox_sandbox_id(user_id),
                    path,
                    deadline_at=deadline_at,
                )
            except AgentBoxApiError as exc:
                if exc.retry not in (
                    RetryDisposition.WAIT,
                    RetryDisposition.SAFE_SAME_OPERATION,
                ):
                    raise
                remaining = (
                    deadline_at - datetime.now(timezone.utc)
                ).total_seconds()
                if remaining <= 0:
                    break
                delay = max(0.05, (exc.retry_after_ms or 250) / 1000)
                await asyncio.sleep(min(delay, remaining))
                continue
            return sandbox_info
        raise TimeoutError(
            f"workspace sandbox {agentbox_sandbox_id(user_id)} did not become usable"
        )

    def _get_manager_client(self) -> AgentBoxClient:
        """Return a process-shared AgentBox manager client.

        Pooled so parallel/sequential tool calls reuse one httpx connection pool
        instead of paying a fresh TLS handshake to the manager on every call. The
        cache key includes the running event loop id so a new client is created
        when settings change or when a different loop is in play (e.g. tests),
        since an httpx.AsyncClient is bound to the loop that created it.
        """
        api_key = settings.agentbox_api_key
        if not api_key:
            raise RuntimeError("AGENTBOX_API_KEY is required for workspace sandboxes")
        key = (settings.agentbox_api_url, api_key, id(asyncio.get_running_loop()))
        cached = WorkspaceSandboxService._shared_manager_client
        if cached is not None and cached[0] == key:
            return cached[1]
        client = AgentBoxClient(
            base_url=settings.agentbox_api_url,
            api_key=api_key,
            timeout_seconds=_SANDBOX_MANAGER_HTTP_TIMEOUT_SECONDS,
            context_headers_provider=correlation_headers,
        )
        WorkspaceSandboxService._shared_manager_client = (key, client)
        return client
