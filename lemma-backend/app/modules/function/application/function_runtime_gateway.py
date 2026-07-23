"""Backend-owned gateway used by the stateless sandbox function runner."""

from __future__ import annotations

import hashlib
import json
from typing import Awaitable, Callable
from urllib.parse import urlparse
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.redaction import redact_text
from app.modules.function.application.function_attempt_credentials import (
    FunctionAttemptCredentialSigner,
)
from app.modules.function.contracts.runtime import (
    RuntimeClaimRequest,
    RuntimeClaimResponse,
    RuntimeEventResponse,
    RuntimeIdentity,
    RuntimeStartedRequest,
    RuntimeTerminalRequest,
)
from app.modules.function.domain.entities import FunctionAttemptRuntimeContext
from app.modules.function.domain.ports import FunctionStorageFactoryPort
from app.modules.function.infrastructure.execution_repository import (
    FunctionExecutionRepository,
)


class RuntimeCredentialRejected(Exception):
    pass


class RuntimeFenceRejected(Exception):
    pass


class RuntimeArtifactCorrupt(Exception):
    pass


TokenMinter = Callable[..., Awaitable[str]]
OrganizationResolver = Callable[[UUID | None], Awaitable[str | None]]


class FunctionRuntimeGateway:
    """Authenticate callbacks and perform each database phase in a short UoW."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        storage_factory: FunctionStorageFactoryPort,
        credential_signer: FunctionAttemptCredentialSigner,
        token_minter: TokenMinter,
        organization_resolver: OrganizationResolver,
        lemma_base_url: str,
        delegated_tokens_enabled: bool,
    ) -> None:
        self._uow_factory = uow_factory
        self._storage_factory = storage_factory
        self._signer = credential_signer
        self._token_minter = token_minter
        self._organization_resolver = organization_resolver
        self._lemma_base_url = self._docker_reachable_url(lemma_base_url)
        self._delegated_tokens_enabled = delegated_tokens_enabled

    async def claim(
        self, ticket: str, request: RuntimeClaimRequest
    ) -> RuntimeClaimResponse:
        # The connection is returned before identity/token providers are called.
        async with self._uow_factory() as uow:
            context = await FunctionExecutionRepository(
                uow, self._signer
            ).claim_ticket(self._signer.digest(ticket))
        if context is None:
            raise RuntimeCredentialRejected
        if request.runtime_abi != context.revision.runtime_abi:
            raise RuntimeFenceRejected("runtime ABI does not match the revision")

        lemma_token = await self._token_minter(
            user_id=context.user_id,
            workload_type="function",
            workload_id=context.function_id,
            pod_id=context.pod_id,
            session_id=str(context.attempt_id),
            workload_name=context.function_name,
            scope=None,
            delegated_tokens_enabled=self._delegated_tokens_enabled,
        )
        organization_id = await self._organization_resolver(context.pod_id)
        return RuntimeClaimResponse(
            attempt_id=context.attempt_id,
            fence=context.fence,
            runtime_token=self._signer.derive(context.attempt_id, "runtime"),
            artifact_url=(
                f"/internal/function-runtime/attempts/{context.attempt_id}/artifact"
            ),
            artifact_sha256=context.revision.artifact_sha256,
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
            lemma_token=lemma_token,
            lemma_base_url=self._lemma_base_url,
            deadline_at=context.deadline_at,
        )

    async def artifact(self, attempt_id: UUID, runtime_token: str) -> bytes:
        context = await self._authorized_context(attempt_id, runtime_token)
        # Object storage is external I/O and deliberately occurs after UoW exit.
        content = await self._storage_factory(context.function_id).read_file(
            context.revision.artifact_path
        )
        data = content.encode("utf-8") if isinstance(content, str) else content
        actual = f"sha256:{hashlib.sha256(data).hexdigest()}"
        if actual != context.revision.artifact_sha256:
            raise RuntimeArtifactCorrupt
        return data

    async def started(
        self,
        attempt_id: UUID,
        runtime_token: str,
        request: RuntimeStartedRequest,
    ) -> RuntimeEventResponse:
        context = await self._authorized_context(attempt_id, runtime_token)
        self._require_fence(context, request.fence)
        async with self._uow_factory() as uow:
            accepted = await FunctionExecutionRepository(
                uow, self._signer
            ).mark_started(context)
        if not accepted:
            raise RuntimeFenceRejected
        return RuntimeEventResponse(accepted=True)

    async def terminal(
        self,
        attempt_id: UUID,
        runtime_token: str,
        request: RuntimeTerminalRequest,
    ) -> RuntimeEventResponse:
        context = await self._authorized_context(attempt_id, runtime_token)
        self._require_fence(context, request.fence)
        payload_hash = hashlib.sha256(
            json.dumps(
                request.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
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
                payload_hash=payload_hash,
                completed=request.status == "completed",
                output_data=request.output_data,
                error=error,
                logs=logs,
            )
        if not accepted:
            raise RuntimeFenceRejected
        return RuntimeEventResponse(accepted=True, duplicate=duplicate)

    async def _authorized_context(
        self, attempt_id: UUID, runtime_token: str
    ) -> FunctionAttemptRuntimeContext:
        async with self._uow_factory() as uow:
            context = await FunctionExecutionRepository(
                uow, self._signer
            ).runtime_context(self._signer.digest(runtime_token))
        if context is None or context.attempt_id != attempt_id:
            raise RuntimeCredentialRejected
        return context

    @staticmethod
    def _require_fence(
        context: FunctionAttemptRuntimeContext, received_fence: int
    ) -> None:
        if context.fence != received_fence:
            raise RuntimeFenceRejected

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

    @staticmethod
    def _docker_reachable_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return url.rstrip("/")
        scheme = parsed.scheme or "http"
        host = "host.docker.internal"
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return f"{scheme}://{host}"
