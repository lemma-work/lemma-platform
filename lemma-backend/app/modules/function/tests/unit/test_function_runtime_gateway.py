from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from uuid import uuid4

import pytest

from app.modules.function.application.function_runtime_gateway import (
    FunctionRuntimeGateway,
    RuntimeArtifactCorrupt,
    RuntimeCredentialRejected,
    RuntimeStateRejected,
    _runtime_failure_message,
)
from app.modules.function.contracts.runtime import (
    RuntimeFailure,
    RuntimeTerminalRequest,
)
from app.modules.function.domain.entities import (
    FunctionRunRuntimeContext,
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


def test_runtime_timeout_without_detail_has_stable_user_facing_error() -> None:
    assert _runtime_failure_message(
        RuntimeFailure(name="TimeoutError", message="")
    ) == "Function execution timed out (deadline exceeded)"


@pytest.mark.asyncio
async def test_gateway_uses_standard_principal_and_releases_uow_before_storage(
    monkeypatch,
) -> None:
    artifact = b"immutable artifact"
    context = _context(artifact)
    state = _UowState()
    terminal_calls = []
    principal = FunctionSessionPrincipal(
        user_id=context.user_id,
        pod_id=context.pod_id,
        function_id=context.function_id,
        session_id="function-session:test",
        actor_name=context.function_name,
    )

    class _Repository:
        def __init__(self, _uow):
            pass

        async def authorize_definition_artifact(
            self,
            function_id,
            revision_hash,
            received_principal,
            **_kwargs,
        ):
            return (
                function_id == context.function_id
                and revision_hash == context.revision_hash
                and received_principal == principal
            )

        async def authorized_runtime_context(
            self,
            run_id,
            received_principal,
            **_kwargs,
        ):
            if run_id == context.run_id and received_principal == principal:
                return context
            return None

        async def complete(self, received, **kwargs):
            duplicate = bool(terminal_calls)
            terminal_calls.append((received, kwargs))
            return None, True, duplicate

    monkeypatch.setattr(
        "app.modules.function.application.function_runtime_gateway."
        "FunctionExecutionRepository",
        _Repository,
    )

    class _Storage:
        async def read_file(self, path):  # pragma: no cover - the artifact
            raise AssertionError(
                "the artifact is binary; read_bytes avoids a whole-buffer "
                "decode attempt that only gets re-encoded"
            )

        async def read_bytes(self, path):
            assert state.active == 0
            assert path == context.artifact_path
            return artifact

    gateway = FunctionRuntimeGateway(
        uow_factory=state.factory,
        storage_factory=lambda _function_id: _Storage(),
        delegated_tokens_enabled=True,
    )
    assert (
        await gateway.definition_artifact(
            context.function_id,
            context.revision_hash,
            principal,
        )
        == artifact
    )
    with pytest.raises(RuntimeCredentialRejected):
        await gateway.definition_artifact(
            context.function_id,
            context.revision_hash,
            principal.model_copy(update={"function_id": uuid4()}),
        )

    request = RuntimeTerminalRequest(
        status="completed",
        output_data={"answer": 42},
        stdout="ok",
        stderr="",
    )
    first = await gateway.terminal(context.run_id, principal, request)
    duplicate = await gateway.terminal(context.run_id, principal, request)

    assert first.accepted and not first.duplicate
    assert duplicate.accepted and duplicate.duplicate
    assert terminal_calls[0][1]["output_data"] == {"answer": 42}
    assert len(terminal_calls) == 2
    assert state.active == 0


@pytest.mark.asyncio
async def test_gateway_rejects_artifact_whose_bytes_do_not_match_the_revision_hash(
    monkeypatch,
) -> None:
    """The gateway re-hashes the stored bytes before serving them, so storage
    corruption (or a path collision) never gets handed to a sandbox as if it
    matched the immutable revision it asked for."""
    artifact = b"immutable artifact"
    context = _context(artifact)
    state = _UowState()
    principal = FunctionSessionPrincipal(
        user_id=context.user_id,
        pod_id=context.pod_id,
        function_id=context.function_id,
        session_id="function-session:test",
        actor_name=context.function_name,
    )

    class _Repository:
        def __init__(self, _uow):
            pass

        async def authorize_definition_artifact(self, *_args, **_kwargs):
            return True

    monkeypatch.setattr(
        "app.modules.function.application.function_runtime_gateway."
        "FunctionExecutionRepository",
        _Repository,
    )

    class _CorruptStorage:
        async def read_bytes(self, path):
            del path
            # Bytes on disk no longer match the hash the caller asked for.
            return artifact + b"tampered"

    gateway = FunctionRuntimeGateway(
        uow_factory=state.factory,
        storage_factory=lambda _function_id: _CorruptStorage(),
        delegated_tokens_enabled=True,
    )

    with pytest.raises(RuntimeArtifactCorrupt):
        await gateway.definition_artifact(
            context.function_id,
            context.revision_hash,
            principal,
        )


@pytest.mark.asyncio
async def test_gateway_terminal_rejects_when_completion_is_not_accepted(
    monkeypatch,
) -> None:
    """A run that ``complete`` declines to transition (already terminal in a
    conflicting state, revoked, etc.) must surface as a rejection rather than a
    silent success."""
    artifact = b"immutable artifact"
    context = _context(artifact)
    state = _UowState()
    principal = FunctionSessionPrincipal(
        user_id=context.user_id,
        pod_id=context.pod_id,
        function_id=context.function_id,
        session_id="function-session:test",
        actor_name=context.function_name,
    )

    class _Repository:
        def __init__(self, _uow):
            pass

        async def authorized_runtime_context(self, run_id, received_principal, **_kw):
            if run_id == context.run_id and received_principal == principal:
                return context
            return None

        async def complete(self, _received, **_kwargs):
            return None, False, False

    monkeypatch.setattr(
        "app.modules.function.application.function_runtime_gateway."
        "FunctionExecutionRepository",
        _Repository,
    )

    gateway = FunctionRuntimeGateway(
        uow_factory=state.factory,
        storage_factory=lambda _function_id: None,
        delegated_tokens_enabled=True,
    )
    request = RuntimeTerminalRequest(
        status="completed",
        output_data={"answer": 42},
        stdout="ok",
        stderr="",
    )

    with pytest.raises(RuntimeStateRejected):
        await gateway.terminal(context.run_id, principal, request)
    assert state.active == 0
