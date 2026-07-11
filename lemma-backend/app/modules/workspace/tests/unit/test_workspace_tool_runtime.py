from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.workspace.services.workspace_tool_runtime import WorkspaceToolRuntime


pytestmark = pytest.mark.asyncio


class FakeEnvCache:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, str]] = {}
        self.set_calls: list[tuple[str, dict[str, str], int]] = []
        self.delete_calls: list[str] = []

    async def get(self, key: str) -> dict[str, str] | None:
        return self.values.get(key)

    async def set(self, key: str, env_vars: dict[str, str], ttl_seconds: int) -> None:
        self.values[key] = env_vars
        self.set_calls.append((key, env_vars, ttl_seconds))

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.delete_calls.append(key)


class FakeWorkspaceService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def get_session(self, **kwargs):
        self.calls.append(kwargs)
        env_vars = kwargs.get("env_vars") or {"LEMMA_TOKEN": "fresh-token"}
        return SimpleNamespace(env_vars=env_vars, session_id=kwargs.get("session_id"))


async def test_workspace_tool_runtime_reuses_cached_function_env():
    cache = FakeEnvCache()
    workspace_service = FakeWorkspaceService()
    runtime = WorkspaceToolRuntime(
        workspace_service=workspace_service,
        env_cache=cache,
        default_env_ttl_seconds=300,
    )
    user_id = uuid4()
    pod_id = uuid4()
    function_id = uuid4()

    first = await runtime.get_session(
        user_id=user_id,
        pod_id=pod_id,
        session_id=f"function-api-{function_id}",
        initial_cwd="/workspace/function",
        close_on_exit=False,
        workload_type="function",
        workload_id=function_id,
    )
    second = await runtime.get_session(
        user_id=user_id,
        pod_id=pod_id,
        session_id=f"function-api-{function_id}",
        initial_cwd="/workspace/function",
        close_on_exit=False,
        workload_type="function",
        workload_id=function_id,
    )

    assert first.env_vars == {"LEMMA_TOKEN": "fresh-token"}
    assert second.env_vars == {"LEMMA_TOKEN": "fresh-token"}
    assert len(workspace_service.calls) == 2
    assert workspace_service.calls[0].get("env_vars") is None
    assert workspace_service.calls[1]["env_vars"] == {"LEMMA_TOKEN": "fresh-token"}
    assert cache.set_calls[0][2] == 300


async def test_workspace_tool_runtime_does_not_share_tokens_across_sessions():
    cache = FakeEnvCache()
    workspace_service = FakeWorkspaceService()
    runtime = WorkspaceToolRuntime(
        workspace_service=workspace_service,
        env_cache=cache,
        default_env_ttl_seconds=300,
    )
    user_id = uuid4()
    pod_id = uuid4()
    function_id = uuid4()

    await runtime.get_session(
        user_id=user_id,
        pod_id=pod_id,
        session_id="session-a",
        workload_type="function",
        workload_id=function_id,
    )
    await runtime.get_session(
        user_id=user_id,
        pod_id=pod_id,
        session_id="session-b",
        workload_type="function",
        workload_id=function_id,
    )

    assert len(cache.set_calls) == 2
    assert cache.set_calls[0][0] != cache.set_calls[1][0]
