"""Compile a function definition without owning run execution.

This collaborator performs storage, schema extraction, and immutable artifact
construction. It deliberately owns no repository or unit of work, so callers
must invoke it between short database phases.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.function.application.function_artifact_builder import (
    FunctionArtifactBuilder,
)
from app.modules.function.domain.entities import (
    FunctionArtifact,
    FunctionEntity,
    FunctionSchemaSet,
)
from app.modules.function.domain.errors import FunctionValidationError
from app.modules.function.domain.ports import (
    FunctionSchemaExecutionPort,
    FunctionStorageFactoryPort,
)


class FunctionDefinitionCompiler:
    """Build artifacts and inspect them through the stateless function runtime."""

    def __init__(
        self,
        *,
        schema_executor: FunctionSchemaExecutionPort,
        storage_factory: FunctionStorageFactoryPort,
    ) -> None:
        self._schema_executor = schema_executor
        self._storage_factory = storage_factory
        self._artifact_builder = FunctionArtifactBuilder(storage_factory)

    async def read_code(self, function_id: UUID, path: str) -> str:
        code = await self._storage_factory(function_id).read_file(path)
        return code.decode("utf-8") if isinstance(code, bytes) else code

    async def write_code(self, function_id: UUID, path: str, code: str) -> None:
        await self._storage_factory(function_id).write_file(path, code)

    async def discard_unused_artifact(self, function: FunctionEntity) -> None:
        artifact = function.pending_artifact
        if (
            function.id is None
            or artifact is None
            or function.code_path == artifact.code_path
        ):
            return
        storage = self._storage_factory(function.id)
        for path in (artifact.artifact_path, artifact.code_path):
            try:
                await storage.delete_file(path)
            except FileNotFoundError:
                # Cleanup is idempotent; a previous attempt may have removed it.
                continue

    async def build_artifact(
        self,
        function: FunctionEntity,
        code: str,
        *,
        python_packages: tuple[str, ...],
    ) -> FunctionArtifact:
        if function.id is None:
            raise FunctionValidationError(
                "Function must be persisted before its revision can be built"
            )
        return await self._artifact_builder.build(
            function_id=function.id,
            code=code,
            python_packages=python_packages,
        )

    async def extract_schemas(
        self,
        function: FunctionEntity,
        artifact: FunctionArtifact,
        *,
        user_id: UUID,
    ) -> FunctionSchemaSet:
        if function.id is None:
            raise FunctionValidationError(
                "Function must be persisted before its schemas can be inspected"
            )
        return await self._schema_executor.extract_schemas(
            function_id=function.id,
            pod_id=function.pod_id,
            user_id=user_id,
            function_name=function.name,
            artifact=artifact,
        )
