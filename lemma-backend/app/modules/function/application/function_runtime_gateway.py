"""Authenticated backend routes used by the stateless function runtime."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.concurrency.offload import run_blocking
from app.core.redaction import redact_text
from app.core.log.log import get_logger
from app.modules.function.contracts.runtime import (
    RuntimeEventResponse,
    RuntimeFailure,
    RuntimeTerminalRequest,
)
from app.modules.function.application.runtime_logs import terminal_logs
from app.modules.function.domain.entities import (
    FunctionArtifact,
    FunctionSessionPrincipal,
)
from app.modules.function.domain.ports import FunctionStorageFactoryPort
from app.modules.function.infrastructure.execution_repository import (
    FunctionExecutionRepository,
)


logger = get_logger(__name__)


class RuntimeCredentialRejected(Exception):
    pass


class RuntimeStateRejected(Exception):
    pass


class RuntimeArtifactCorrupt(Exception):
    pass


#: Builds the repository this gateway reads through, from one open unit of
#: work. Injected for the reason `uow_factory` and `storage_factory` already
#: are: it is the gateway's third collaborator, and a test that reaches into
#: the module to replace it is doubling the subject rather than isolating it.
RepositoryFactory = Callable[[SqlAlchemyUnitOfWork], FunctionExecutionRepository]


class FunctionRuntimeGateway:
    """Authorize artifact reads and JOB terminal reports with function auth."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        storage_factory: FunctionStorageFactoryPort,
        delegated_tokens_enabled: bool,
        repository_factory: RepositoryFactory,
    ) -> None:
        self._uow_factory = uow_factory
        self._storage_factory = storage_factory
        self._delegated_tokens_enabled = delegated_tokens_enabled
        self._repository = repository_factory

    async def definition_artifact(
        self,
        function_id: UUID,
        revision_hash: str,
        principal: FunctionSessionPrincipal,
        *,
        generation: UUID | None = None,
    ) -> bytes:
        """Return one exact immutable artifact authorized by standard claims."""

        async with self._uow_factory() as uow:
            repository = self._repository(uow)
            authorized = await repository.authorize_definition_artifact(
                function_id,
                revision_hash,
                principal,
                delegated_tokens_enabled=self._delegated_tokens_enabled,
            )
            if authorized and generation is None:
                generation = await repository.artifact_generation(
                    function_id, revision_hash
                )
        if not authorized:
            raise RuntimeCredentialRejected
        artifact = FunctionArtifact(revision_hash=revision_hash, generation=generation)
        data = await self._artifact_bytes(function_id, artifact)
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

    async def _artifact_bytes(
        self, function_id: UUID, artifact: FunctionArtifact
    ) -> bytes:
        """The artifact's bytes, whichever generation staged them.

        Every artifact is written under `artifact-uploads/<generation>/`, and a
        reader learns that generation one of two ways: the runtime sends it as
        `X-Lemma-Artifact-Generation`, or the `function_revisions` row records
        it. During a build neither is available. The row is written only after
        schema extraction succeeds, and a sandbox runtime older than the
        generation contract does not send the header -- so create and update
        resolved the pre-generation path, missed, and returned 503 with the
        function left in DRAFT. It does not self-heal: every later attempt on
        that pod builds a new generation and misses again.

        So when the generation is unknown, find it. The filename is the content
        digest, which is what makes this safe rather than a guess: a match is
        the requested bytes by construction, and the caller verifies the sha256
        of what comes back regardless.
        """
        storage = self._storage_factory(function_id)
        try:
            return await storage.read_bytes(artifact.artifact_path)
        except FileNotFoundError:
            if artifact.generation is not None:
                raise
        staged = [
            path
            for path in await storage.list_prefix(artifact.STAGED_PREFIX)
            if artifact.matches_staged_path(path)
        ]
        if not staged:
            raise FileNotFoundError(f"File {artifact.artifact_path} not found")
        logger.info(
            "function.function_runtime_gateway.artifact_generation_recovered",
            function_id=str(function_id),
            revision_hash=artifact.revision_hash,
            candidate_count=len(staged),
        )
        return await storage.read_bytes(staged[0])

    async def terminal(
        self,
        run_id: UUID,
        principal: FunctionSessionPrincipal,
        request: RuntimeTerminalRequest,
    ) -> RuntimeEventResponse:
        async with self._uow_factory() as uow:
            context = await self._repository(uow).authorized_runtime_context(
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
            _run, accepted, duplicate = await self._repository(uow).complete(
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
