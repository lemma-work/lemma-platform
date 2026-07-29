"""Authenticated backend routes used by the stateless function runtime."""

from __future__ import annotations

import hashlib
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.redaction import redact_text
from app.modules.function.contracts.runtime import (
    RuntimeEventResponse,
    RuntimeFailure,
    RuntimeTerminalRequest,
)
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
        artifact_path = (
            f"artifacts/{revision_hash.removeprefix('sha256:')}.zip"
        )
        content = await self._storage_factory(function_id).read_file(artifact_path)
        data = content.encode("utf-8") if isinstance(content, str) else content
        actual = f"sha256:{hashlib.sha256(data).hexdigest()}"
        if actual != revision_hash:
            raise RuntimeArtifactCorrupt
        return data

    async def terminal(
        self,
        run_id: UUID,
        principal: FunctionSessionPrincipal,
        request: RuntimeTerminalRequest,
    ) -> RuntimeEventResponse:
        async with self._uow_factory() as uow:
            context = await FunctionExecutionRepository(
                uow
            ).authorized_runtime_context(
                run_id,
                principal,
                delegated_tokens_enabled=self._delegated_tokens_enabled,
            )
        if context is None:
            raise RuntimeCredentialRejected
        logs = self._logs(request)
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

    @staticmethod
    def _logs(request: RuntimeTerminalRequest) -> str | None:
        sections: list[str] = []
        if request.stdout:
            sections.append(request.stdout)
        if request.stderr:
            sections.append(request.stderr)
        if request.output_truncated:
            sections.append("[function output truncated]")
        if not sections:
            return None
        return redact_text("\n".join(sections))[: 4 * 1024 * 1024]


def _runtime_failure_message(error: RuntimeFailure) -> str:
    # asyncio.wait_for raises the built-in TimeoutError without a message. The
    # runtime preserves the exception type, so normalize that empty detail into
    # the stable timeout semantic expected by API and job function clients.
    if error.name == "TimeoutError":
        return "Function execution timed out (deadline exceeded)"
    return redact_text(f"{error.name}: {error.message}")[:16_384]
