from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.core.config import settings
from app.modules.agent.domain.workspace_entities import SandboxInfo
from app.core.authorization.delegation import (
    CLAIM_ACTOR_ID,
    CLAIM_ACTOR_NAME,
    CLAIM_ACTOR_TYPE,
)
from app.modules.agent.domain.workspace_entities import WorkspaceStatus
from app.modules.workspace.services.workspace_activity_store import WorkspaceActivity
from app.modules.workspace.services import workspace_sandbox_service
from app.modules.workspace.services.workspace_sandbox_service import WorkspaceSandboxService
from app.modules.workspace.services.workspace_state_store import WorkspaceState


class _FakeSandbox:
    def __init__(self, infos: dict[UUID, SandboxInfo]):
        self.infos = infos
        self.deleted: list[UUID] = []
        self.ensured: list[UUID] = []
        self.ensure_envs: list[dict[str, str] | None] = []
        self.heartbeats: list[UUID] = []

    async def heartbeat(self, user_id: UUID) -> None:
        self.heartbeats.append(user_id)

    async def ensure_sandbox(
        self,
        user_id: UUID,
        *,
        env: dict[str, str] | None = None,
    ) -> SandboxInfo:
        self.ensured.append(user_id)
        self.ensure_envs.append(env)
        info = self.infos[user_id]
        info.status = "RUNNING"
        return info

    async def get_sandbox(self, user_id: UUID) -> SandboxInfo | None:
        return self.infos.get(user_id)

    async def delete_sandbox(self, user_id: UUID) -> None:
        self.deleted.append(user_id)

    async def is_sandbox_running(self, user_id: UUID) -> bool:
        info = self.infos.get(user_id)
        return info is not None and info.status == "RUNNING"


class _FakeActivityStore:
    def __init__(self):
        self.marked: list[dict] = []
        self.removed: list[UUID] = []

    async def mark_active(self, **kwargs) -> None:
        self.marked.append(kwargs)

    async def list_stale(self, *, runtime: str, idle_seconds: int) -> list[WorkspaceActivity]:
        del runtime, idle_seconds
        return []

    async def remove(self, *, runtime: str, user_id: UUID) -> None:
        del runtime
        self.removed.append(user_id)


class _SequenceSandbox:
    """Fake sandbox whose ``get_sandbox`` walks a scripted status sequence.

    The last status is held once the sequence is exhausted, which lets a test
    model "pod is terminal for a while, then becomes RUNNING" (or stays terminal
    forever). ``ensure_sandbox`` records its calls and returns ``ensure_status``.
    """

    def __init__(
        self,
        statuses: list[str | None],
        *,
        ensure_status: str = "CREATING",
    ):
        self._statuses = list(statuses)
        self.ensure_status = ensure_status
        self.ensured: list[UUID] = []
        self.ensure_envs: list[dict[str, str] | None] = []
        self.get_calls = 0

    async def get_sandbox(self, user_id: UUID) -> SandboxInfo | None:
        self.get_calls += 1
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        if status is None:
            return None
        return _sandbox_info(user_id, status=status)

    async def ensure_sandbox(
        self,
        user_id: UUID,
        *,
        env: dict[str, str] | None = None,
    ) -> SandboxInfo:
        self.ensured.append(user_id)
        self.ensure_envs.append(env)
        return _sandbox_info(user_id, status=self.ensure_status)

    async def delete_sandbox(self, user_id: UUID) -> None:
        del user_id

    async def is_sandbox_running(self, user_id: UUID) -> bool:
        del user_id
        return False


class _FakeStateStore:
    def __init__(
        self,
        *,
        lock_acquired: bool = True,
        creation_in_progress: bool = True,
    ):
        self.lock_acquired = lock_acquired
        self.creation_in_progress = creation_in_progress
        self.lock_released = False
        self.states: list[str] = []
        self.errors: list[str] = []
        self.current_state = None

    async def acquire_creation_lock(
        self, *, runtime: str, user_id: UUID, owner: str, timeout_seconds: int
    ) -> bool:
        del runtime, user_id, owner, timeout_seconds
        return self.lock_acquired

    async def is_creation_in_progress(self, *, runtime: str, user_id: UUID) -> bool:
        del runtime, user_id
        return self.creation_in_progress

    async def release_creation_lock(self, *, runtime: str, user_id: UUID, owner: str) -> None:
        del runtime, user_id, owner
        self.lock_released = True

    async def mark_creating(self, *, runtime: str, user_id: UUID, ttl_seconds: int = 0) -> None:
        del runtime, user_id, ttl_seconds
        self.states.append("CREATING")

    async def mark_running(self, **kwargs) -> None:
        del kwargs
        self.states.append("RUNNING")

    async def mark_error(
        self, *, runtime: str, user_id: UUID, error: str, ttl_seconds: int = 0
    ) -> None:
        del runtime, user_id, ttl_seconds
        self.states.append("ERROR")
        self.errors.append(error)

    async def mark_stopped(self, *, runtime: str, user_id: UUID, ttl_seconds: int = 0) -> None:
        del runtime, user_id, ttl_seconds
        self.states.append("STOPPED")

    async def get_state(self, *, runtime: str, user_id: UUID):
        del runtime, user_id
        return self.current_state


def _sandbox_info(user_id: UUID, *, status: str = "RUNNING") -> SandboxInfo:
    return SandboxInfo(
        sandbox_id=user_id.hex,
        name=f"agentbox-{user_id.hex}",
        namespace="agentbox",
        status=status,
        image="agentbox-runtime:latest",
        endpoint=f"agentbox://{user_id.hex}",
    )


def test_service_uses_agentbox_runtime_label(monkeypatch):
    monkeypatch.setattr(settings, "environment", "testing")

    service = WorkspaceSandboxService(
        sandbox=_FakeSandbox({}),  # type: ignore[arg-type]
        activity_store=_FakeActivityStore(),  # type: ignore[arg-type]
        state_store=_FakeStateStore(),  # type: ignore[arg-type]
    )

    assert service.runtime == "agentbox"


@pytest.mark.asyncio
async def test_get_or_create_sandbox_prefers_existing_running_sandbox():
    user_id = uuid4()
    sandbox = _FakeSandbox({user_id: _sandbox_info(user_id)})
    activity_store = _FakeActivityStore()
    state_store = _FakeStateStore()
    service = WorkspaceSandboxService(
        runtime="agentbox",
        sandbox=sandbox,  # type: ignore[arg-type]
        activity_store=activity_store,  # type: ignore[arg-type]
        state_store=state_store,  # type: ignore[arg-type]
    )

    result = await service.get_or_create_sandbox(user_id)

    assert result["workspace_url"] == f"agentbox://{user_id.hex}"
    assert result["sandbox_id"] == user_id.hex
    assert sandbox.ensured == []
    assert activity_store.marked
    # Fast path (reuse a RUNNING sandbox) must reset the manager idle clock so a
    # near-idle sandbox is not reaped out from under a sessionless run.
    assert sandbox.heartbeats == [user_id]


@pytest.mark.asyncio
async def test_get_or_create_sandbox_heartbeats_on_handout_to_avoid_idle_reap():
    """Regression for the concurrent-run "Sandbox not found" 404: reusing an
    already-RUNNING sandbox resets its idle deadline at handout."""
    user_id = uuid4()
    sandbox = _FakeSandbox({user_id: _sandbox_info(user_id)})
    service = WorkspaceSandboxService(
        runtime="agentbox",
        sandbox=sandbox,  # type: ignore[arg-type]
        activity_store=_FakeActivityStore(),  # type: ignore[arg-type]
        state_store=_FakeStateStore(),  # type: ignore[arg-type]
    )

    await service.get_or_create_sandbox(user_id)
    await service.get_or_create_sandbox(user_id)

    # Every handout resets the idle clock; reuse never skips it.
    assert sandbox.heartbeats == [user_id, user_id]
    assert sandbox.ensured == []


@pytest.mark.asyncio
async def test_get_or_create_sandbox_passes_container_app_env(monkeypatch):
    user_id = uuid4()
    sandbox = _FakeSandbox({user_id: _sandbox_info(user_id, status="CREATING")})
    monkeypatch.setattr(settings, "api_url", "http://localhost:8711")
    service = WorkspaceSandboxService(
        runtime="docker",
        sandbox=sandbox,  # type: ignore[arg-type]
        activity_store=_FakeActivityStore(),  # type: ignore[arg-type]
        state_store=_FakeStateStore(),  # type: ignore[arg-type]
    )

    await service.get_or_create_sandbox(user_id)

    assert sandbox.ensure_envs == [
        {"LEMMA_BASE_URL": "http://host.docker.internal:8711"}
    ]


@pytest.mark.asyncio
async def test_get_or_create_sandbox_prefers_cli_api_url_for_local_https(monkeypatch):
    user_id = uuid4()
    sandbox = _FakeSandbox({user_id: _sandbox_info(user_id, status="CREATING")})
    monkeypatch.setattr(settings, "api_url", "https://127-0-0-1.sslip.io:8743")
    monkeypatch.setattr(settings, "cli_api_url", "http://127-0-0-1.sslip.io:8710")
    service = WorkspaceSandboxService(
        runtime="docker",
        sandbox=sandbox,  # type: ignore[arg-type]
        activity_store=_FakeActivityStore(),  # type: ignore[arg-type]
        state_store=_FakeStateStore(),  # type: ignore[arg-type]
    )

    await service.get_or_create_sandbox(user_id)

    assert sandbox.ensure_envs == [
        {"LEMMA_BASE_URL": "http://host.docker.internal:8710"}
    ]


@pytest.mark.asyncio
async def test_get_or_create_sandbox_prefers_workspace_callback_api_url(monkeypatch):
    user_id = uuid4()
    sandbox = _FakeSandbox({user_id: _sandbox_info(user_id, status="CREATING")})
    monkeypatch.setattr(settings, "api_url", "http://localhost:8711")
    monkeypatch.setattr(settings, "workspace_callback_api_url", "http://backend:8000")
    service = WorkspaceSandboxService(
        runtime="docker",
        sandbox=sandbox,  # type: ignore[arg-type]
        activity_store=_FakeActivityStore(),  # type: ignore[arg-type]
        state_store=_FakeStateStore(),  # type: ignore[arg-type]
    )

    await service.get_or_create_sandbox(user_id)

    assert sandbox.ensure_envs == [{"LEMMA_BASE_URL": "http://backend:8000"}]


@pytest.mark.asyncio
async def test_get_or_create_sandbox_marks_error_on_creation_failure():
    user_id = uuid4()

    class _FailingSandbox(_FakeSandbox):
        async def get_sandbox(self, user_id: UUID) -> SandboxInfo | None:
            del user_id
            return None

        async def ensure_sandbox(
            self,
            user_id: UUID,
            *,
            env: dict[str, str] | None = None,
        ) -> SandboxInfo:
            del user_id, env
            raise RuntimeError("boom")

    state_store = _FakeStateStore(lock_acquired=True)
    service = WorkspaceSandboxService(
        runtime="agentbox",
        sandbox=_FailingSandbox({}),  # type: ignore[arg-type]
        activity_store=_FakeActivityStore(),  # type: ignore[arg-type]
        state_store=state_store,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="boom"):
        await service.get_or_create_sandbox(user_id)

    assert state_store.lock_released is True
    assert state_store.states == ["CREATING", "ERROR"]


@pytest.mark.asyncio
async def test_waiter_tolerates_transient_terminal_status_during_heal(monkeypatch):
    """A parallel caller that lost the creation lock must NOT fail when it
    observes a transient terminal pod status while the lock holder heals it."""
    monkeypatch.setattr(workspace_sandbox_service, "_CREATE_WAIT_POLL_SECONDS", 0)
    user_id = uuid4()
    # Pod is terminal for the first few reads (lock holder mid-recreate), then
    # comes up RUNNING. The waiter must keep waiting through the ERROR readings.
    sandbox = _SequenceSandbox(["ERROR", "ERROR", "ERROR", "RUNNING"])
    # lock_acquired=False -> this caller is a waiter; creation still in progress.
    state_store = _FakeStateStore(lock_acquired=False, creation_in_progress=True)
    service = WorkspaceSandboxService(
        runtime="agentbox",
        sandbox=sandbox,  # type: ignore[arg-type]
        activity_store=_FakeActivityStore(),  # type: ignore[arg-type]
        state_store=state_store,  # type: ignore[arg-type]
    )

    result = await service.get_or_create_sandbox(user_id)

    assert result["status"] == "RUNNING"
    # The waiter never ran ensure itself and never raised on the terminal reads.
    assert sandbox.ensured == []


@pytest.mark.asyncio
async def test_waiter_surfaces_recorded_creation_error(monkeypatch):
    """A waiter fails only on a genuine, recorded creation error from the lock
    holder — not on a transient terminal pod status."""
    monkeypatch.setattr(workspace_sandbox_service, "_CREATE_WAIT_POLL_SECONDS", 0)
    user_id = uuid4()
    sandbox = _SequenceSandbox(["ERROR"])  # stays terminal
    state_store = _FakeStateStore(lock_acquired=False, creation_in_progress=True)
    state_store.current_state = WorkspaceState(
        user_id=user_id,
        runtime="agentbox",
        status=WorkspaceStatus.ERROR,
        updated_at=datetime.now(timezone.utc),
        error="pod crashed: OOMKilled",
    )
    service = WorkspaceSandboxService(
        runtime="agentbox",
        sandbox=sandbox,  # type: ignore[arg-type]
        activity_store=_FakeActivityStore(),  # type: ignore[arg-type]
        state_store=state_store,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="OOMKilled"):
        await service.get_or_create_sandbox(user_id)


@pytest.mark.asyncio
async def test_waiter_takes_over_when_creator_vanished(monkeypatch):
    """If the lock holder released the lock without finishing (e.g. its process
    died) and recorded no error, a waiter takes over and creates the sandbox."""
    monkeypatch.setattr(workspace_sandbox_service, "_CREATE_WAIT_POLL_SECONDS", 0)
    user_id = uuid4()
    sandbox = _SequenceSandbox(["STOPPED"], ensure_status="RUNNING")
    # First acquire fails (someone holds it), but creation is NOT in progress
    # (lock already gone) -> waiter returns None and the loop re-acquires.
    state_store = _FakeStateStore(lock_acquired=False, creation_in_progress=False)

    # On the takeover iteration the caller must win the lock to heal it.
    calls = {"n": 0}

    async def _acquire(*, runtime, user_id, owner, timeout_seconds):
        del runtime, user_id, owner, timeout_seconds
        calls["n"] += 1
        return calls["n"] >= 2  # lose first round, win the takeover round

    state_store.acquire_creation_lock = _acquire  # type: ignore[assignment]

    service = WorkspaceSandboxService(
        runtime="agentbox",
        sandbox=sandbox,  # type: ignore[arg-type]
        activity_store=_FakeActivityStore(),  # type: ignore[arg-type]
        state_store=state_store,  # type: ignore[arg-type]
    )

    result = await service.get_or_create_sandbox(user_id)

    assert result["status"] == "RUNNING"
    assert sandbox.ensured == [user_id]  # the waiter healed it once after takeover


@pytest.mark.asyncio
async def test_get_env_vars_includes_delegation_claims(monkeypatch):
    user_id = uuid4()
    pod_id = uuid4()
    org_id = uuid4()
    workload_id = uuid4()
    captured: dict = {}

    async def _fake_get_user_token(user_id, delegation_claims=None):
        captured["user_id"] = user_id
        captured["delegation_claims"] = delegation_claims
        return "delegated-token"

    monkeypatch.setattr(
        "app.composition.workspace_identity.get_user_token",
        _fake_get_user_token,
    )
    monkeypatch.setattr(
        WorkspaceSandboxService,
        "_resolve_organization_id",
        lambda self, _pod_id: asyncio.sleep(0, result=str(org_id)),
    )
    monkeypatch.setattr(settings, "authz_delegated_tokens_enabled", True)
    monkeypatch.setattr(settings, "api_url", "http://localhost:8711")
    monkeypatch.setattr(settings, "auth_frontend_url", "http://localhost:4173")
    monkeypatch.setattr(settings, "frontend_url", "http://localhost:3711")

    service = WorkspaceSandboxService(
        runtime="docker",
        sandbox=_FakeSandbox({}),  # type: ignore[arg-type]
        activity_store=_FakeActivityStore(),  # type: ignore[arg-type]
        state_store=_FakeStateStore(),  # type: ignore[arg-type]
    )
    env = await service.get_env_vars(
        user_id=user_id,
        pod_id=pod_id,
        workload_type="function",
        workload_id=workload_id,
        workload_name="table_sync",
        scope=["datastore.read"],
        session_id="session-123",
    )

    claims = captured["delegation_claims"]
    assert env["LEMMA_TOKEN"] == "delegated-token"
    assert env["LEMMA_USER_ID"] == str(user_id)
    assert env["LEMMA_POD_ID"] == str(pod_id)
    assert env["LEMMA_ORG_ID"] == str(org_id)
    assert env["LEMMA_BASE_URL"] == "http://host.docker.internal:8711"
    assert env["LEMMA_AUTH_URL"] == "http://host.docker.internal:4173"
    assert env["LEMMA_HOST_ORIGIN"] == "http://host.docker.internal:3711"
    assert claims[CLAIM_ACTOR_TYPE] == "function"
    assert claims[CLAIM_ACTOR_ID] == str(workload_id)
    assert claims[CLAIM_ACTOR_NAME] == "table_sync"


@pytest.mark.asyncio
async def test_get_session_uses_sandbox_manager_even_for_docker_runtime(monkeypatch):
    user_id = uuid4()
    sandbox_info = _sandbox_info(user_id)
    sandbox_info.endpoint = "http://127.0.0.1:39001"
    sandbox = _FakeSandbox({user_id: sandbox_info})

    async def _fake_get_env_vars(*args, **kwargs):
        del args, kwargs
        return {"LEMMA_TOKEN": "delegated-token"}

    service = WorkspaceSandboxService(
        runtime="docker",
        sandbox=sandbox,  # type: ignore[arg-type]
        activity_store=_FakeActivityStore(),  # type: ignore[arg-type]
        state_store=_FakeStateStore(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(service, "get_env_vars", _fake_get_env_vars)

    session = await service.get_session(
        user_id=user_id,
        pod_id=None,
        session_id="conversation-session",
    )

    assert session.session_id == "conversation-session"
    assert session.client.base_url == settings.agentbox_api_url
