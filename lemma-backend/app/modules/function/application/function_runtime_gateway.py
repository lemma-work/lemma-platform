"""Authenticated backend routes used by the stateless function runtime."""

from __future__ import annotations

import hashlib
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.concurrency.offload import run_blocking
from app.core.redaction import redact_text
from app.modules.function.contracts.runtime import (
    RuntimeEventResponse,
    RuntimeFailure,
    RuntimeTerminalRequest,
)
from app.modules.function.application.runtime_logs import terminal_logs
from app.modules.function.domain.entities import FunctionSessionPrincipal
from app.modules.function.domain.ports import FunctionStorageFactoryPort
from app.modules.function.infrastructure.execution_repository import (
    FunctionExecutionRepository,
)


class RuntimeCredentialRejected(Exception):
    pass


class RuntimeStateRejected(Exception):
    pass


class RuntimeArtifactCorrupt(Exception):
    pass


class FunctionRuntimeGateway:
    """Authorize artifact reads and JOB terminal reports with function auth."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        storage_factory: FunctionStorageFactoryPort,
        delegated_tokens_enabled: bool,
    ) -> None:
        self._uow_factory = uow_factory
        self._storage_factory = storage_factory
        self._delegated_tokens_enabled = delegated_tokens_enabled

    async def definition_artifact(
        self,
        function_id: UUID,
        revision_hash: str,
        principal: FunctionSessionPrincipal,
    ) -> bytes:
        """Return one exact immutable artifact authorized by standard claims."""

        async with self._uow_factory() as uow:
            authorized = await FunctionExecutionRepository(
                uow
            ).authorize_definition_artifact(
                function_id,
                revision_hash,
                principal,
                delegated_tokens_enabled=self._delegated_tokens_enabled,
            )
        if not authorized:
            raise RuntimeCredentialRejected
        artifact_path = f"artifacts/{revision_hash.removeprefix('sha256:')}.zip"
        data = await self._storage_factory(function_id).read_bytes(artifact_path)
        # Offloaded for the reason the builder already documents at its own
        # sha256 (`function_artifact_builder.py`): the artifact is the whole
        # bundle, user code plus resolved site-packages, so it grows with the
        # dependency tree. Hashing it inline held the event loop for the length
        # of a multi-megabyte digest on every fetch -- and every sandbox fetches
        # its bundle on every cold start, so this stalled unrelated requests on
        # the same worker at exactly the moment the platform was busiest.
        digest = await run_blocking(lambda: hashlib.sha256(data).hexdigest())
        if f"sha256:{digest}" != revision_hash:
            raise RuntimeArtifactCorrupt
        return data

    async def terminal(
        self,
        run_id: UUID,
        principal: FunctionSessionPrincipal,
        request: RuntimeTerminalRequest,
    ) -> RuntimeEventResponse:
        async with self._uow_factory() as uow:
            context = await FunctionExecutionRepository(uow).authorized_runtime_context(
                run_id,
                principal,
                delegated_tokens_enabled=self._delegated_tokens_enabled,
            )
        if context is None:
            raise RuntimeCredentialRejected
        # Off the loop, like the dispatcher's copy: this is up to 4 MiB of regex
        # over a payload whose size the sandbox chose, arriving on the public
        # callback endpoint.
        logs = await run_blocking(self._logs, request, limiter="cpu_bound")
        error = (
            _runtime_failure_message(request.error)
            if request.error is not None
            else None
        )
        async with self._uow_factory() as uow:
            _run, accepted, duplicate = await FunctionExecutionRepository(uow).complete(
                context,
                completed=request.status == "completed",
                output_data=request.output_data,
                error=error,
                logs=logs,
            )
        if not accepted:
            raise RuntimeStateRejected
        return RuntimeEventResponse(accepted=True, duplicate=duplicate)

    _logs = staticmethod(terminal_logs)


def _runtime_failure_message(error: RuntimeFailure) -> str:
    # asyncio.wait_for raises the built-in TimeoutError without a message. The
    # runtime preserves the exception type, so normalize that empty detail into
    # the stable timeout semantic expected by API and job function clients.
    if error.name == "TimeoutError":
        return "Function execution timed out (deadline exceeded)"
    return redact_text(f"{error.name}: {error.message}")[:16_384]
