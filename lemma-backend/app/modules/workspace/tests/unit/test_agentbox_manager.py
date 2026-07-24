from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from agentbox_client import (
    AgentBoxApiError,
    ProfileRef,
    RetryDisposition,
    SandboxHandle,
    WorkloadKind,
)
from agentbox_client.models import AgentBoxErrorBody, AgentBoxErrorResponse
from app.modules.workspace.services.agentbox_manager import AgentBoxSandbox


def _api_error(retry: RetryDisposition) -> AgentBoxApiError:
    response = httpx.Response(
        503,
        request=httpx.Request("PUT", "http://agentbox.test/sandboxes/workspace/id"),
    )
    return AgentBoxApiError(
        response,
        AgentBoxErrorResponse(
            error=AgentBoxErrorBody(
                code="PROVIDER_UNAVAILABLE",
                message="provider allocation failed and was removed",
                retry=retry,
            )
        ),
    )


def _ready_sandbox(logical_id):
    return SandboxHandle(
        workload_kind=WorkloadKind.WORKSPACE,
        logical_id=logical_id,
        desired_state="active",
        profile=ProfileRef(
            name="workspace-python-v1",
            digest=f"sha256:{'a' * 64}",
        ),
        allocation_state="active",
        allocation_id=uuid4(),
        allocation_epoch=2,
        ready=True,
        operation_id=None,
        retry_after_ms=None,
    )


@pytest.mark.asyncio
async def test_workspace_retries_safe_same_operation(monkeypatch):
    user_id = uuid4()

    class _Client:
        calls = 0

        async def ensure_sandbox(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise _api_error(RetryDisposition.SAFE_SAME_OPERATION)
            return _ready_sandbox(user_id)

    sandbox = object.__new__(AgentBoxSandbox)
    sandbox.client = _Client()

    async def no_wait(*_args, **_kwargs):
        return None

    monkeypatch.setattr(AgentBoxSandbox, "_wait", no_wait)
    result = await sandbox.ensure_sandbox(user_id)

    assert result.status == "RUNNING"
    assert sandbox.client.calls == 2


@pytest.mark.asyncio
async def test_workspace_does_not_retry_unsafe_operation(monkeypatch):
    user_id = uuid4()

    class _Client:
        calls = 0

        async def ensure_sandbox(self, *_args, **_kwargs):
            self.calls += 1
            raise _api_error(RetryDisposition.DO_NOT_RETRY)

    sandbox = object.__new__(AgentBoxSandbox)
    sandbox.client = _Client()

    async def no_wait(*_args, **_kwargs):
        return None

    monkeypatch.setattr(AgentBoxSandbox, "_wait", no_wait)
    with pytest.raises(AgentBoxApiError):
        await sandbox.ensure_sandbox(user_id)

    assert sandbox.client.calls == 1
