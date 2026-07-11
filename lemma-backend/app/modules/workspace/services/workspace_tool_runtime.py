"""Lazy workspace runtime for CLI tools."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import httpx

from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)
from app.modules.workspace.services.workspace_env_cache import (
    RedisWorkspaceEnvCache,
    WorkspaceEnvCachePort,
    get_default_workspace_env_ttl_seconds,
)
from app.modules.workspace.services.workspace_process_store import WorkspaceProcessStore
from app.modules.workspace.services.interfaces import IWorkspaceSession

_workspace_tool_runtime: "WorkspaceToolRuntime | None" = None
_function_workspace_runtime: "WorkspaceToolRuntime | None" = None
_DEFAULT_SCOPE = "default"


def _decode_jwt_expiry(token: str | None) -> Optional[datetime]:
    if not token:
        return None
    try:
        segments = token.split(".")
        if len(segments) < 2:
            return None
        payload = segments[1]
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode((payload + padding).encode("ascii")).decode(
            "utf-8"
        )
        data = json.loads(decoded)
        exp = data.get("exp")
        if not isinstance(exp, (int, float)):
            return None
        return datetime.fromtimestamp(float(exp), tz=timezone.utc)
    except Exception:
        return None


class WorkspaceToolRuntime:
    def __init__(
        self,
        *,
        workspace_service: WorkspaceSandboxService | None = None,
        env_cache: WorkspaceEnvCachePort | None = None,
        process_store: WorkspaceProcessStore | None = None,
        default_env_ttl_seconds: int | None = None,
    ):
        self.workspace_service = workspace_service or WorkspaceSandboxService()
        self.env_cache = env_cache or RedisWorkspaceEnvCache()
        self.process_store = process_store or WorkspaceProcessStore()
        self.default_env_ttl_seconds = (
            default_env_ttl_seconds or get_default_workspace_env_ttl_seconds()
        )

    def _build_env_cache_key(
        self,
        *,
        user_id: UUID,
        scope_key: str | None,
        scope: list[str] | None,
        pod_id: UUID | None,
        organization_id: UUID | None,
        workload_type: str | None,
        workload_id: UUID | None,
        workload_name: str | None,
        session_id: str | None,
    ) -> str:
        parts = [
            str(user_id),
            scope_key or _DEFAULT_SCOPE,
            str(pod_id) if pod_id else "none",
            str(organization_id) if organization_id else "none",
            workload_type or "none",
            str(workload_id) if workload_id else "none",
            workload_name or "none",
            ",".join(sorted(scope or [])) or "none",
            session_id or "none",
        ]
        return ":".join(parts)

    def _resolve_env_ttl_seconds(self, env_vars: dict[str, str]) -> int:
        token_expiry = _decode_jwt_expiry(env_vars.get("LEMMA_TOKEN"))
        if token_expiry is None:
            return self.default_env_ttl_seconds

        now = datetime.now(timezone.utc)
        seconds_until_expiry = int((token_expiry - now).total_seconds())
        if seconds_until_expiry <= 0:
            return 1
        return min(self.default_env_ttl_seconds, seconds_until_expiry)

    def _get_cache_key(
        self,
        *,
        user_id: UUID,
        pod_id: UUID | None,
        organization_id: UUID | None,
        workload_type: str | None,
        workload_id: UUID | None,
        workload_name: str | None,
        scope_key: str | None,
        scope: list[str] | None,
        session_id: str | None,
    ) -> str:
        return self._build_env_cache_key(
            user_id=user_id,
            scope_key=scope_key,
            scope=scope,
            pod_id=pod_id,
            organization_id=organization_id,
            workload_type=workload_type,
            workload_id=workload_id,
            workload_name=workload_name,
            session_id=session_id,
        )

    async def get_session(
        self,
        user_id: UUID,
        pod_id: UUID | None,
        session_id: str | None = None,
        initial_cwd: str = "/workspace",
        close_on_exit: bool = True,
        workload_type: str | None = None,
        workload_id: UUID | None = None,
        scope: list[str] | None = None,
        organization_id: UUID | None = None,
        workload_name: str | None = None,
        scope_key: str | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> IWorkspaceSession:
        cache_key = self._get_cache_key(
            user_id=user_id,
            pod_id=pod_id,
            organization_id=organization_id,
            workload_type=workload_type,
            workload_id=workload_id,
            workload_name=workload_name,
            scope_key=scope_key,
            scope=scope,
            session_id=session_id,
        )
        cached_env_vars = env_vars or await self.env_cache.get(cache_key)

        if cached_env_vars is not None:
            try:
                return await self.workspace_service.get_session(
                    user_id=user_id,
                    pod_id=pod_id,
                    organization_id=organization_id,
                    session_id=session_id,
                    initial_cwd=initial_cwd,
                    close_on_exit=close_on_exit,
                    workload_type=workload_type,
                    workload_id=workload_id,
                    workload_name=workload_name,
                    scope=scope,
                    env_vars=cached_env_vars,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {401, 403}:
                    raise
                await self.env_cache.delete(cache_key)

        session = await self.workspace_service.get_session(
            user_id=user_id,
            pod_id=pod_id,
            organization_id=organization_id,
            session_id=session_id,
            initial_cwd=initial_cwd,
            close_on_exit=close_on_exit,
            workload_type=workload_type,
            workload_id=workload_id,
            workload_name=workload_name,
            scope=scope,
        )
        if session.env_vars:
            await self.env_cache.set(
                cache_key,
                session.env_vars,
                ttl_seconds=self._resolve_env_ttl_seconds(session.env_vars),
            )
        return session

    async def bind_process_to_session(
        self,
        *,
        process_id: str,
        session_id: str,
        ttl_seconds: int = 60 * 30,
    ) -> None:
        await self.process_store.set_session_id(
            process_id=process_id,
            session_id=session_id,
            ttl_seconds=ttl_seconds,
        )

    async def resolve_session_for_process(self, process_id: str) -> str | None:
        return await self.process_store.get_session_id(process_id)

    async def clear_process_binding(self, process_id: str) -> None:
        await self.process_store.delete(process_id)

    async def close(self) -> None:
        await self.env_cache.close()
        await self.process_store.close()
        close = getattr(self.workspace_service, "close", None)
        if close is not None:
            await close()


def get_workspace_tool_runtime() -> WorkspaceToolRuntime:
    global _workspace_tool_runtime
    if _workspace_tool_runtime is None:
        _workspace_tool_runtime = WorkspaceToolRuntime()
    return _workspace_tool_runtime


def get_function_workspace_runtime() -> WorkspaceToolRuntime:
    global _function_workspace_runtime
    if _function_workspace_runtime is None:
        _function_workspace_runtime = WorkspaceToolRuntime(
            default_env_ttl_seconds=5 * 60
        )
    return _function_workspace_runtime


def reset_workspace_tool_runtimes() -> None:
    """Reset cached workspace runtimes so tests can swap AgentBox endpoints."""
    global _workspace_tool_runtime, _function_workspace_runtime
    _workspace_tool_runtime = None
    _function_workspace_runtime = None


async def close_workspace_tool_runtimes() -> None:
    """Close and reset cached workspace runtimes."""
    global _workspace_tool_runtime, _function_workspace_runtime
    runtimes = [
        runtime
        for runtime in (_workspace_tool_runtime, _function_workspace_runtime)
        if runtime is not None
    ]
    _workspace_tool_runtime = None
    _function_workspace_runtime = None
    for runtime in runtimes:
        await runtime.close()


async def invalidate_function_workspace_env_cache(
    *,
    pod_id: UUID,
    function_id: UUID,
) -> None:
    """Drop delegated-token env cache entries for a function workload."""
    cache = RedisWorkspaceEnvCache()
    try:
        await cache.delete_matching(f"*:*:{pod_id}:*:function:{function_id}:*")
    finally:
        await cache.close()
