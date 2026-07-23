from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
from uuid import uuid4

import pytest

from app.modules.function.application.function_attempt_credentials import (
    FunctionAttemptCredentialSigner,
)
from app.modules.function.application.function_runtime_gateway import (
    FunctionRuntimeGateway,
)
from app.modules.function.contracts.runtime import (
    RuntimeClaimRequest,
    RuntimeStartedRequest,
    RuntimeTerminalRequest,
)
from app.modules.function.domain.entities import (
    FunctionAttemptRuntimeContext,
    FunctionRevisionEntity,
    FunctionRevisionStatus,
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


def _context(artifact: bytes) -> FunctionAttemptRuntimeContext:
    function_id = uuid4()
    return FunctionAttemptRuntimeContext(
        attempt_id=uuid4(),
        run_id=uuid4(),
        fence=4,
        operation_id=uuid4(),
        deadline_at="2099-01-01T00:00:00Z",
        revision=FunctionRevisionEntity(
            id=uuid4(),
            function_id=function_id,
            revision_number=1,
            status=FunctionRevisionStatus.READY,
            code_sha256=f"sha256:{'1' * 64}",
            artifact_sha256=f"sha256:{hashlib.sha256(artifact).hexdigest()}",
            artifact_path="artifacts/function.zip",
            runtime_abi="lemma-function-python-1",
            builder_digest="test-builder",
            manifest={},
        ),
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
    signer = FunctionAttemptCredentialSigner("g" * 32)
    state = _UowState()
    terminal_calls = []

    class _Repository:
        def __init__(self, _uow, _signer):
            pass

        async def claim_ticket(self, _digest):
            return context

        async def runtime_context(self, _digest):
            return context

        async def mark_started(self, received):
            assert received == context
            return True

        async def complete(self, received, **kwargs):
            terminal_calls.append((received, kwargs))
            return None, True, False

    monkeypatch.setattr(
        "app.modules.function.application.function_runtime_gateway."
        "FunctionExecutionRepository",
        _Repository,
    )

    class _Storage:
        async def read_file(self, path):
            assert state.active == 0
            assert path == "artifacts/function.zip"
            return artifact

    async def token_minter(**_kwargs):
        assert state.active == 0
        return "lemma-token"

    async def organization_resolver(_pod_id):
        assert state.active == 0
        return str(uuid4())

    gateway = FunctionRuntimeGateway(
        uow_factory=state.factory,
        storage_factory=lambda _function_id: _Storage(),
        credential_signer=signer,
        token_minter=token_minter,
        organization_resolver=organization_resolver,
        lemma_base_url="https://api.lemma.test",
        delegated_tokens_enabled=True,
    )
    ticket = signer.derive(context.attempt_id, "ticket")
    claim = await gateway.claim(
        ticket, RuntimeClaimRequest(runtime_abi="lemma-function-python-1")
    )
    assert claim.attempt_id == context.attempt_id
    assert claim.runtime_token == signer.derive(context.attempt_id, "runtime")
    assert await gateway.artifact(context.attempt_id, claim.runtime_token) == artifact
    started = await gateway.started(
        context.attempt_id,
        claim.runtime_token,
        RuntimeStartedRequest(fence=context.fence),
    )
    assert started.accepted
    terminal = await gateway.terminal(
        context.attempt_id,
        claim.runtime_token,
        RuntimeTerminalRequest(
            fence=context.fence,
            status="completed",
            output_data={"answer": 42},
            stdout="ok",
            stderr="",
        ),
    )
    assert terminal.accepted
    assert terminal_calls[0][1]["output_data"] == {"answer": 42}
    assert state.active == 0
