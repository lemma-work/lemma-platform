"""Workspace sandbox service for AgentBox and local Docker runtimes."""

from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import urlparse
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.request_context import correlation_headers
from agentbox_client import AgentBoxClient
from app.modules.workspace.contracts import SandboxInfo, WorkspaceStatus
from app.modules.workspace.agentbox_session import AgentBoxWorkspaceSession
from app.modules.workspace.agentbox_retry import retry_on_transient_agentbox_error
from app.modules.workspace.services.agentbox_manager import (
    AgentBoxSandbox,
    agentbox_sandbox_id,
)
from app.modules.workspace.services.interfaces import ISandbox, IWorkspaceSession
from app.modules.workspace.services.workspace_activity_store import (
    WorkspaceActivityStore,
)
from app.modules.workspace.services.workspace_state_store import WorkspaceStateStore
from app.core.log.log import get_logger

logger = get_logger(__name__)

_activity_store: WorkspaceActivityStore | None = None
_state_store: WorkspaceStateStore | None = None
_CREATE_LOCK_TTL_SECONDS = 180
_CREATE_WAIT_TIMEOUT_SECONDS = 180
_CREATE_WAIT_POLL_SECONDS = 1
# When the pod we own settles in a terminal state, re-trigger the self-healing
# ensure at most this often (ensure is idempotent and recreates dead pods).
_ENSURE_RETRY_COOLDOWN_SECONDS = 10
_SANDBOX_MANAGER_HTTP_TIMEOUT_SECONDS = 300.0
_TERMINAL_SANDBOX_STATUSES = frozenset({"ERROR", "STOPPED"})


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

    async def _get_sandbox_info(self, user_id: UUID) -> SandboxInfo | None:
        return await self.sandbox.get_sandbox(user_id)

    async def _ensure_sandbox_info(self, user_id: UUID) -> SandboxInfo:
        return await self.sandbox.ensure_sandbox(
            user_id,
            env=self._get_sandbox_app_env(),
        )

    async def _ensure_sandbox_info_with_retry(self, user_id: UUID) -> SandboxInfo:
        """Ensure the sandbox, retrying only transient manager errors.

        The manager proxy can briefly return retryable 5xx / connection errors
        while it is bringing the sandbox up. Those are retried with backoff;
        genuine 4xx and non-transport errors propagate immediately so real
        failures surface at once.
        """

        def _log_retry(attempt: int, error: str) -> None:
            logger.debug(
                "workspace.workspace_sandbox_service.ensure_sandbox_not_ready_yet.observed",
                user_id=user_id,
                attempt=attempt,
            )

        return await retry_on_transient_agentbox_error(
            lambda: self._ensure_sandbox_info(user_id),
            on_retry=_log_retry,
        )

    def _get_sandbox_app_env(self) -> dict[str, str]:
        return {
            "LEMMA_BASE_URL": self._resolve_workspace_api_url(),
        }

    def _resolve_workspace_api_url(self) -> str:
        return self.resolve_workspace_api_url_for_runtime(self.runtime)

    @staticmethod
    def resolve_workspace_api_url_for_runtime(runtime: str) -> str:
        if settings.workspace_callback_api_url:
            return settings.workspace_callback_api_url
        return WorkspaceSandboxService.resolve_workspace_host_url_for_runtime(
            runtime,
            settings.cli_api_url or settings.api_url,
        )

    @staticmethod
    def _is_loopback_host(hostname: str | None) -> bool:
        if not hostname:
            return False
        if hostname in {"localhost", "127.0.0.1", "0.0.0.0"}:
            return True
        # The local dev stack addresses the host via the sslip.io dashed-IP
        # loopback alias (e.g. 127-0-0-1.sslip.io -> 127.0.0.1). From inside a
        # container that still points at the container itself, so it must be
        # rewritten to host.docker.internal like any other loopback host.
        if hostname.endswith(".sslip.io"):
            candidate = hostname.split(".", 1)[0].replace("-", ".")
            return candidate.startswith("127.") or candidate == "0.0.0.0"
        return False

    @staticmethod
    def resolve_workspace_host_url_for_runtime(runtime: str, url: str) -> str:
        if runtime not in {"docker", "agentbox"}:
            return url
        parsed = urlparse(url)
        if not WorkspaceSandboxService._is_loopback_host(parsed.hostname):
            return url
        scheme = parsed.scheme or "http"
        netloc = "host.docker.internal"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return f"{scheme}://{netloc}"

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

    async def get_or_create_sandbox(self, user_id: UUID) -> dict:
        """Return a RUNNING sandbox for the user, creating/healing as needed.

        This is the single funnel every workspace session goes through. It is
        resilient to:
        - cold starts (sandbox not yet created),
        - terminal pods (crashed/OOM/stopped) — healed via the idempotent
          ensure, never by deleting the persistent sandbox entry,
        - concurrent callers — a single creator holds the creation lock and the
          others wait for the sandbox to come up instead of failing on a
          transient terminal status they observe mid-heal.
        """
        sandbox_info = await self._get_sandbox_info(user_id)
        if sandbox_info and sandbox_info.status == "RUNNING":
            return await self._build_running_sandbox_response(user_id, sandbox_info)

        sandbox_info = await self._ensure_running_sandbox(user_id)
        return await self._build_running_sandbox_response(user_id, sandbox_info)

    async def _ensure_running_sandbox(self, user_id: UUID) -> SandboxInfo:
        """Drive the sandbox to RUNNING, coordinating concurrent callers.

        Exactly one caller holds the creation lock and runs ensure+wait; the
        rest wait for it. If the lock holder genuinely fails it records the error
        and the waiters surface it; if the lock holder simply vanishes (process
        died, lock expired) a waiter takes over. Everything is bounded by
        ``_CREATE_WAIT_TIMEOUT_SECONDS``.
        """
        deadline = asyncio.get_running_loop().time() + _CREATE_WAIT_TIMEOUT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            lock_owner = str(uuid4())
            has_create_lock = await self.state_store.acquire_creation_lock(
                runtime=self.runtime,
                user_id=user_id,
                owner=lock_owner,
                timeout_seconds=_CREATE_LOCK_TTL_SECONDS,
            )
            if has_create_lock:
                try:
                    return await self._create_running_sandbox(user_id, deadline)
                finally:
                    await self.state_store.release_creation_lock(
                        runtime=self.runtime,
                        user_id=user_id,
                        owner=lock_owner,
                    )

            running = await self._wait_for_creator(user_id, deadline)
            if running is not None:
                return running
            # Creator vanished without reaching RUNNING; loop to take over.

        raise TimeoutError(
            f"Workspace sandbox for user {user_id} did not reach Running state in "
            f"{_CREATE_WAIT_TIMEOUT_SECONDS}s"
        )

    async def _create_running_sandbox(
        self,
        user_id: UUID,
        deadline: float,
    ) -> SandboxInfo:
        """Lock-holder path: ensure the sandbox and wait until it is RUNNING."""
        await self.state_store.mark_creating(runtime=self.runtime, user_id=user_id)
        try:
            # ensure_sandbox is idempotent and self-healing in AgentBox: it
            # recreates a pod stuck in a terminal state rather than returning a
            # dead one, so we ensure then wait for RUNNING.
            sandbox_info = await self._ensure_sandbox_info_with_retry(user_id)
            if sandbox_info.status != "RUNNING":
                sandbox_info = await self._poll_until_running(user_id, deadline)
            return sandbox_info
        except Exception as exc:
            await self.state_store.mark_error(
                runtime=self.runtime,
                user_id=user_id,
                error=str(exc),
            )
            raise

    async def _poll_until_running(self, user_id: UUID, deadline: float) -> SandboxInfo:
        """Wait for the sandbox we own to report RUNNING.

        A pod can briefly report a terminal status right after ensure triggers a
        delete+recreate (the old, dead pod is still observable until the new one
        appears). We therefore treat terminal statuses as transient and keep
        waiting, periodically re-running the self-healing ensure rather than
        giving up on the first terminal reading.
        """
        last_ensure = asyncio.get_running_loop().time()
        while asyncio.get_running_loop().time() < deadline:
            sandbox_info = await self._get_sandbox_info(user_id)
            if sandbox_info and sandbox_info.status == "RUNNING":
                return sandbox_info

            now = asyncio.get_running_loop().time()
            if (
                sandbox_info
                and sandbox_info.status in _TERMINAL_SANDBOX_STATUSES
                and now - last_ensure >= _ENSURE_RETRY_COOLDOWN_SECONDS
            ):
                logger.debug(
                    "workspace.workspace_sandbox_service.workspace_sandbox_user_s_still.observed",
                    user_id=user_id,
                    status=sandbox_info.status,
                )
                await self._ensure_sandbox_info_with_retry(user_id)
                last_ensure = now

            await asyncio.sleep(_CREATE_WAIT_POLL_SECONDS)

        raise TimeoutError(
            f"Workspace sandbox for user {user_id} did not reach Running state in "
            f"{_CREATE_WAIT_TIMEOUT_SECONDS}s"
        )

    async def _wait_for_creator(
        self,
        user_id: UUID,
        deadline: float,
    ) -> SandboxInfo | None:
        """Waiter path: wait for the lock holder to bring the sandbox up.

        Returns the RUNNING ``SandboxInfo`` on success, or ``None`` when the
        creator released the lock without reaching RUNNING and without recording
        an error (so the caller can take over). Raises if the creator recorded a
        genuine creation failure, or on timeout. Crucially, a transient terminal
        *pod* status is ignored while creation is in progress — only a recorded
        state-store error counts as a real failure.
        """
        while asyncio.get_running_loop().time() < deadline:
            sandbox_info = await self._get_sandbox_info(user_id)
            if sandbox_info and sandbox_info.status == "RUNNING":
                return sandbox_info

            state = await self.state_store.get_state(
                runtime=self.runtime,
                user_id=user_id,
            )
            if state and state.status == WorkspaceStatus.ERROR:
                raise RuntimeError(
                    state.error
                    or f"Workspace sandbox creation failed for user {user_id} on runtime {self.runtime}"
                )

            if not await self.state_store.is_creation_in_progress(
                runtime=self.runtime,
                user_id=user_id,
            ):
                # Lock gone but no error recorded and not RUNNING: re-check the
                # pod once (covers the create-success → lock-release race) then
                # hand back to the caller to take over creation.
                sandbox_info = await self._get_sandbox_info(user_id)
                if sandbox_info and sandbox_info.status == "RUNNING":
                    return sandbox_info
                return None

            await asyncio.sleep(_CREATE_WAIT_POLL_SECONDS)

        raise TimeoutError(
            f"Workspace sandbox for user {user_id} did not reach Running state in "
            f"{_CREATE_WAIT_TIMEOUT_SECONDS}s"
        )

    async def _build_running_sandbox_response(
        self,
        user_id: UUID,
        sandbox_info: SandboxInfo,
    ) -> dict:
        # Reset the manager's idle clock at handout. The fast path returns a
        # RUNNING sandbox without re-running ensure, so a sandbox that was
        # already near its idle deadline (idle_since_at close to the timeout)
        # would otherwise be reaped seconds into the run -- before a sessionless
        # workload (function run) ever creates a session or its keepalive
        # heartbeat first fires. Marking it active here gives the caller a fresh
        # idle window. Best-effort: never block session handout on it.
        try:
            await self.sandbox.heartbeat(user_id)
        except Exception:
            logger.debug(
                "workspace.workspace_sandbox_service.handout_heartbeat_user_s_runtime.observed",
                user_id=user_id,
            )
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
        return {
            "sandbox_id": sandbox_info.sandbox_id,
            "name": sandbox_info.sandbox_id,
            "status": sandbox_info.status,
            "workspace_url": sandbox_info.endpoint,
            "container_name": sandbox_info.sandbox_id,
        }

    async def stop_sandbox(self, user_id: UUID) -> None:
        sandbox_info = await self._get_sandbox_info(user_id)
        await self._delete_sandbox(user_id, sandbox_info)
        await self.activity_store.remove(runtime=self.runtime, user_id=user_id)
        await self.state_store.mark_stopped(runtime=self.runtime, user_id=user_id)

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
        auth_url = self._resolve_workspace_host_url(
            settings.cli_auth_frontend_url or settings.auth_frontend_url
        )
        host_origin = self._resolve_workspace_host_url(settings.frontend_url)

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

    def _resolve_workspace_host_url(self, url: str) -> str:
        return self.resolve_workspace_host_url_for_runtime(self.runtime, url)

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
        sandbox_response = await self.get_or_create_sandbox(user_id)
        sandbox_info = SandboxInfo(
            sandbox_id=str(sandbox_response["sandbox_id"]),
            name=str(sandbox_response["name"]),
            namespace=None,
            status=str(sandbox_response["status"]),
            image="",
            created_at=None,
            endpoint=str(sandbox_response["workspace_url"]),
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
            sandbox_id=agentbox_sandbox_id(user_id),
            session_id=session_id,
            env_vars=env_vars,
            initial_cwd=initial_cwd,
            auto_close=close_on_exit,
            activity_callback=_activity_callback,
            owns_client=False,
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
