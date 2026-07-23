"""Backend-owned gateway used by the stateless sandbox function runner."""

from __future__ import annotations

import hashlib
from typing import Awaitable, Callable
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.redaction import redact_text
from app.modules.function.application.function_callback_credentials import (
    FunctionCallbackCredentialSigner,
)
from app.modules.function.contracts.runtime import (
    RuntimeClaimRequest,
    RuntimeClaimResponse,
    RuntimeEventResponse,
    RuntimeIdentity,
    RuntimeTerminalRequest,
)
from app.modules.function.domain.entities import (
    FunctionRunRuntimeContext,
    FunctionSessionPrincipal,
)
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


OrganizationResolver = Callable[[UUID | None], Awaitable[str | None]]


class FunctionRuntimeGateway:
    """Authenticate callbacks and perform each database phase in a short UoW."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        storage_factory: FunctionStorageFactoryPort,
        credential_signer: FunctionCallbackCredentialSigner,
        organization_resolver: OrganizationResolver,
        lemma_base_url: str,
        delegated_tokens_enabled: bool,
    ) -> None:
        self._uow_factory = uow_factory
        self._storage_factory = storage_factory
        self._signer = credential_signer
        self._organization_resolver = organization_resolver
        self._lemma_base_url = lemma_base_url.rstrip("/")
        self._delegated_tokens_enabled = delegated_tokens_enabled

    async def claim(
        self,
        function_token: str,
        principal: FunctionSessionPrincipal,
        run_id: UUID,
        request: RuntimeClaimRequest,
    ) -> RuntimeClaimResponse:
        async with self._uow_factory() as uow:
            context = await FunctionExecutionRepository(
                uow, self._signer
            ).claim_execution(
                run_id,
                principal,
                revision_hash=request.revision_hash,
                input_data=request.input_data,
                delegated_tokens_enabled=self._delegated_tokens_enabled,
            )
        if context is None:
            raise RuntimeCredentialRejected
        # Organization lookup is external to this UoW and cannot pin a DB
        # connection while it resolves.
        organization_id = await self._organization_resolver(context.pod_id)
        return RuntimeClaimResponse(
            run_id=context.run_id,
            callback_token=self._signer.derive(context.run_id),
            artifact_url=(
                f"/internal/function-runtime/runs/{context.run_id}/artifact"
            ),
            revision_hash=context.revision_hash,
            input_data=context.input_data,
            config=context.config,
            identity=RuntimeIdentity(
                user_id=context.user_id,
                user_email=context.user_email,
                pod_id=context.pod_id,
                function_id=context.function_id,
                function_name=context.function_name,
                organization_id=UUID(organization_id) if organization_id else None,
            ),
            lemma_token=function_token,
            lemma_base_url=self._lemma_base_url,
            deadline_at=context.deadline_at,
        )

    async def artifact(self, run_id: UUID, callback_token: str) -> bytes:
        context = await self._active_context(run_id, callback_token)
        # Object storage is external I/O and deliberately occurs after UoW exit.
        content = await self._storage_factory(context.function_id).read_file(
            context.artifact_path
        )
        data = content.encode("utf-8") if isinstance(content, str) else content
        actual = f"sha256:{hashlib.sha256(data).hexdigest()}"
        if actual != context.revision_hash:
            raise RuntimeArtifactCorrupt
        return data

    async def terminal(
        self,
        run_id: UUID,
        callback_token: str,
        request: RuntimeTerminalRequest,
    ) -> RuntimeEventResponse:
        context = await self._authorized_context(run_id, callback_token)
        logs = self._logs(request)
        error = None
        if request.error is not None:
            error = redact_text(
                f"{request.error.name}: {request.error.message}"
            )[:16_384]
        async with self._uow_factory() as uow:
            _run, accepted, duplicate = await FunctionExecutionRepository(
                uow, self._signer
            ).complete(
                context,
                completed=request.status == "completed",
                output_data=request.output_data,
                error=error,
                logs=logs,
                timings=request.timings,
            )
        if not accepted:
            raise RuntimeStateRejected
        return RuntimeEventResponse(accepted=True, duplicate=duplicate)

    async def _authorized_context(
        self, run_id: UUID, callback_token: str
    ) -> FunctionRunRuntimeContext:
        async with self._uow_factory() as uow:
            context = await FunctionExecutionRepository(
                uow, self._signer
            ).runtime_context(run_id, callback_token)
        if context is None:
            raise RuntimeCredentialRejected
        return context

    async def _active_context(
        self, run_id: UUID, callback_token: str
    ) -> FunctionRunRuntimeContext:
        async with self._uow_factory() as uow:
            context = await FunctionExecutionRepository(
                uow, self._signer
            ).active_runtime_context(run_id, callback_token)
        if context is None:
            raise RuntimeCredentialRejected
        return context

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
