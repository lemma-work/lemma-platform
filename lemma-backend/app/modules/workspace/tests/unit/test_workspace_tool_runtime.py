from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from sandbox_runtime.paths import WORKSPACE_ROOT
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
        initial_cwd=f"{WORKSPACE_ROOT}/function",
        close_on_exit=False,
        workload_type="function",
        workload_id=function_id,
    )
    second = await runtime.get_session(
        user_id=user_id,
        pod_id=pod_id,
        session_id=f"function-api-{function_id}",
        initial_cwd=f"{WORKSPACE_ROOT}/function",
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


def _host_pack_urls() -> dict[str, str]:
    """The Desktop host pack's URL environment, from the contract Rust pins."""
    import json
    from pathlib import Path

    for parent in Path(__file__).resolve().parents:
        candidate = parent / "desktop" / "contracts" / "host-pack-urls.json"
        if candidate.exists():
            return json.loads(candidate.read_text())["backend_env"]
    raise AssertionError("desktop/contracts/host-pack-urls.json was not found")


async def test_a_host_command_gets_addresses_the_mac_resolves_and_its_conversation(
    monkeypatch: pytest.MonkeyPatch,
):
    """`lemma` in a host command acts as the run, against the Mac's backend.

    The delegated environment is minted for the VM, whose containers reach the
    backend as `host.lemma.internal`. A command on the Mac given that address
    fails to resolve it, so the host session carries the addresses the host
    pack gives the CLI instead -- read from what the host pack really emits.
    """
    from app.core.config import settings
    from app.modules.workspace.config import workspace_settings
    from app.modules.workspace.services.workspace_sandbox_service import (
        WorkspaceSandboxService,
    )

    emitted = {
        name: template.replace("{base}", "lemma.localhost").replace("{port}", "52502")
        for name, template in _host_pack_urls().items()
    }
    for name, value in emitted.items():
        target = (
            workspace_settings if name.startswith("WORKSPACE_CALLBACK_") else settings
        )
        monkeypatch.setattr(target, name.lower(), value)
    monkeypatch.setattr(settings, "cli_api_url", None)
    monkeypatch.setattr(settings, "cli_auth_frontend_url", None)

    async def mint(**_: object) -> str:
        return "the-runs-own-session"

    monkeypatch.setattr(
        "app.modules.identity.contracts.delegated_tokens.mint_delegated_token", mint
    )
    monkeypatch.setattr(
        "app.modules.workspace.services.sandbox_composition.build_local_client",
        lambda: SimpleNamespace(),
    )
    service = WorkspaceSandboxService()
    runtime = WorkspaceToolRuntime(
        workspace_service=service, env_cache=FakeEnvCache(), default_env_ttl_seconds=60
    )
    conversation_id = uuid4()
    try:
        session = await runtime.get_host_session(
            user_id=uuid4(),
            pod_id=uuid4(),
            organization_id=uuid4(),
            sandbox_id=uuid4(),
            root="/Users/me/lemma/c/2026-09-25/demo",
            session_id=f"shell-{conversation_id.hex}",
            conversation_id=conversation_id,
        )
    finally:
        await service.close()

    env = session.env_vars
    assert env["LEMMA_TOKEN"] == "the-runs-own-session"
    assert env["LEMMA_CONVERSATION_ID"] == str(conversation_id)
    assert env["LEMMA_BASE_URL"] == emitted["API_URL"]
    assert env["LEMMA_AUTH_URL"] == emitted["AUTH_FRONTEND_URL"]
    assert env["LEMMA_HOST_ORIGIN"] == emitted["FRONTEND_URL"]
    assert "LEMMA_WORKSPACE_URL" not in env
    assert not [name for name, value in env.items() if "host.lemma.internal" in value]


def _jwt(claims: dict[str, object]) -> str:
    import base64
    import json

    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
    return f"header.{body.rstrip('=')}.signature"


async def test_a_short_lived_token_is_cached_for_half_its_remaining_life():
    """Every command writes the cached token to the session's file, and a
    process reading it from there should find one with time left."""
    import time

    from app.core.authorization.delegation import CLAIM_EXPIRES_AT, CLAIM_ISSUED_AT

    now = time.time()
    runtime = WorkspaceToolRuntime(
        workspace_service=FakeWorkspaceService(),
        env_cache=FakeEnvCache(),
        default_env_ttl_seconds=300,
    )
    token = _jwt({"exp": now + 3600, CLAIM_ISSUED_AT: now, CLAIM_EXPIRES_AT: now + 240})

    ttl = runtime._resolve_env_ttl_seconds({"LEMMA_TOKEN": token})

    assert 115 <= ttl <= 120


async def test_a_cached_token_from_an_ended_conversation_is_minted_again():
    """Ending a conversation refuses its tokens; one still cached would be
    handed to the conversation's next run and refused by every call it made."""
    import time

    from app.core.authorization.delegation import (
        CLAIM_EXPIRES_AT,
        CLAIM_ISSUED_AT,
        CLAIM_SESSION_ID,
    )

    now = time.time()
    asked: list[tuple[str, float]] = []

    async def ended(session_id: str, issued_at: float) -> bool:
        asked.append((session_id, issued_at))
        return True

    cache = FakeEnvCache()
    workspace_service = FakeWorkspaceService()
    runtime = WorkspaceToolRuntime(
        workspace_service=workspace_service,
        env_cache=cache,
        default_env_ttl_seconds=300,
        session_ended=ended,
    )
    user_id, pod_id = uuid4(), uuid4()
    key = runtime._get_cache_key(
        user_id=user_id,
        pod_id=pod_id,
        organization_id=None,
        workload_type=None,
        workload_id=None,
        workload_name=None,
        scope_key=None,
        scope=None,
        session_id="shell-c",
    )
    spent = _jwt(
        {
            CLAIM_SESSION_ID: "conversation",
            CLAIM_ISSUED_AT: now,
            CLAIM_EXPIRES_AT: now + 900,
        }
    )
    cache.values[key] = {"LEMMA_TOKEN": spent}

    session = await runtime.get_session(
        user_id=user_id, pod_id=pod_id, session_id="shell-c"
    )

    assert asked == [("conversation", now)]
    assert cache.delete_calls == [key]
    assert workspace_service.calls[0].get("env_vars") is None
    assert session.env_vars == {"LEMMA_TOKEN": "fresh-token"}


async def test_a_cached_hour_long_token_is_not_checked_against_session_ends():
    async def ended(session_id: str, issued_at: float) -> bool:
        raise AssertionError("a token without a lifetime was looked up")

    cache = FakeEnvCache()
    workspace_service = FakeWorkspaceService()
    runtime = WorkspaceToolRuntime(
        workspace_service=workspace_service,
        env_cache=cache,
        default_env_ttl_seconds=300,
        session_ended=ended,
    )
    user_id = uuid4()
    await runtime.get_session(user_id=user_id, pod_id=None, session_id="s")
    await runtime.get_session(user_id=user_id, pod_id=None, session_id="s")

    assert workspace_service.calls[1]["env_vars"] == {"LEMMA_TOKEN": "fresh-token"}
