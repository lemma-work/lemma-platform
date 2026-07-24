from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from uuid import uuid4

import pytest

from app.modules.function.application.function_callback_credentials import (
    FunctionCallbackCredentialSigner,
)
from app.modules.function.application.function_runtime_gateway import (
    FunctionRuntimeGateway,
    RuntimeCredentialRejected,
)
from app.modules.function.contracts.runtime import (
    RuntimeClaimRequest,
    RuntimeTerminalRequest,
)
from app.modules.function.domain.entities import (
    FunctionRunEntity,
    FunctionRunRuntimeContext,
    FunctionRunStatus,
    FunctionSessionPrincipal,
)


class _UowState:
    active = 0

    @asynccontextmanager
    async def factory(self):
        self.active += 1
        try:
            yield object()
        finally:
            self.active -= 1


def _context(artifact: bytes) -> FunctionRunRuntimeContext:
    function_id = uuid4()
    revision_hash = f"sha256:{hashlib.sha256(artifact).hexdigest()}"
    return FunctionRunRuntimeContext(
        run_id=uuid4(),
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        revision_hash=revision_hash,
        artifact_path=f"artifacts/{revision_hash.removeprefix('sha256:')}.zip",
        input_data={"value": 3},
        config={"mode": "test"},
        user_id=uuid4(),
        user_email="person@example.com",
        pod_id=uuid4(),
        function_id=function_id,
        function_name="calculate",
    )


@pytest.mark.asyncio
async def test_gateway_releases_database_before_identity_and_storage_io(
    monkeypatch,
) -> None:
    artifact = b"immutable artifact"
    context = _context(artifact)
    signer = FunctionCallbackCredentialSigner("g" * 32)
    state = _UowState()
    terminal_calls = []
    telemetry_calls = []
    now = datetime.now(timezone.utc)
    persisted_run = FunctionRunEntity(
        id=context.run_id,
        function_id=context.function_id,
        user_id=context.user_id,
        status=FunctionRunStatus.COMPLETED,
        created_at=now - timedelta(seconds=1),
        completed_at=now,
    )

    class _Repository:
        def __init__(self, _uow, _signer):
            pass

        async def claim_execution(self, *_args, **_kwargs):
            return context

        async def runtime_context(self, run_id, callback_token):
            if (
                run_id == context.run_id
                and callback_token == signer.derive(context.run_id)
            ):
                return context
            return None

        async def active_runtime_context(self, run_id, callback_token):
            return await self.runtime_context(run_id, callback_token)

        async def complete(self, received, **kwargs):
            duplicate = bool(terminal_calls)
            terminal_calls.append((received, kwargs))
            return persisted_run, True, duplicate

    monkeypatch.setattr(
        "app.modules.function.application.function_runtime_gateway."
        "FunctionExecutionRepository",
        _Repository,
    )
    monkeypatch.setattr(
        "app.modules.function.application.function_runtime_gateway.record_terminal",
        lambda *args, **kwargs: telemetry_calls.append((args, kwargs)),
    )

    class _Storage:
        async def read_file(self, path):
            assert state.active == 0
            assert path == context.artifact_path
            return artifact

    async def organization_resolver(_pod_id):
        assert state.active == 0
        return str(uuid4())

    gateway = FunctionRuntimeGateway(
        uow_factory=state.factory,
        storage_factory=lambda _function_id: _Storage(),
        credential_signer=signer,
        organization_resolver=organization_resolver,
        lemma_base_url="https://api.lemma.test",
        delegated_tokens_enabled=True,
    )
    function_token = "delegated-function-token"
    principal = FunctionSessionPrincipal(
        user_id=context.user_id,
        pod_id=context.pod_id,
        function_id=context.function_id,
        session_id="function-session:test",
    )
    claim = await gateway.claim(
        function_token,
        principal,
        context.run_id,
        RuntimeClaimRequest(
            revision_hash=context.revision_hash,
            input_data=context.input_data,
        ),
    )
    assert claim.run_id == context.run_id
    assert claim.callback_token == signer.derive(context.run_id)
    assert claim.lemma_token == function_token
    assert await gateway.artifact(context.run_id, claim.callback_token) == artifact

    terminal_request = RuntimeTerminalRequest(
        status="completed",
        output_data={"answer": 42},
        stdout="ok",
        stderr="",
    )
    terminal = await gateway.terminal(
        context.run_id,
        claim.callback_token,
        terminal_request,
    )
    duplicate = await gateway.terminal(
        context.run_id,
        claim.callback_token,
        terminal_request,
    )

    assert terminal.accepted and not terminal.duplicate
    assert duplicate.accepted and duplicate.duplicate
    assert terminal_calls[0][1]["output_data"] == {"answer": 42}
    assert len(terminal_calls) == 2
    assert len(telemetry_calls) == 1
    with pytest.raises(RuntimeCredentialRejected):
        await gateway.artifact(context.run_id, "wrong-callback-token")
    assert state.active == 0
