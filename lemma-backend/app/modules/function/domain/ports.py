"""Function module ports."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.function.domain.entities import (
    FunctionArtifact,
    FunctionDispatchMode,
    FunctionEntity,
    FunctionRunEntity,
    FunctionSchemaSet,
)


class FunctionRepositoryPort(Protocol):
    async def create(self, entity: FunctionEntity) -> FunctionEntity: ...

    async def get(self, id: UUID) -> FunctionEntity | None: ...

    async def get_by_name(
        self, pod_id: UUID, name: str, ctx: Context | None = None
    ) -> FunctionEntity | None: ...

    async def update(self, function: FunctionEntity) -> FunctionEntity: ...

    async def activate_revision_if_missing(
        self,
        function_id: UUID,
        *,
        expected_code_path: str,
        revision_hash: str,
        code_path: str,
    ) -> FunctionEntity | None: ...

    async def delete(self, id: UUID) -> bool: ...

    async def list_by_pod(
        self, pod_id: UUID, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[FunctionEntity], str | None]: ...

    async def list_visible_by_pod(
        self,
        pod_id: UUID,
        ctx: Context,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[FunctionEntity], str | None]: ...


class FunctionRunRepositoryPort(Protocol):
    async def create_run(self, entity: FunctionRunEntity) -> FunctionRunEntity: ...

    async def get_run(self, run_id: UUID) -> FunctionRunEntity | None: ...

    async def list_runs_by_function(
        self, function_id: UUID, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[FunctionRunEntity], str | None]: ...


class FunctionStoragePort(Protocol):
    async def read_file(self, path: str) -> bytes | str: ...

    #: For a caller that knows the content is binary, so it does not pay a
    #: whole-buffer UTF-8 decode attempt only to re-encode the result.
    async def read_bytes(self, path: str) -> bytes: ...

    async def write_file(self, path: str, content: bytes | str) -> None: ...


class FunctionStorageFactoryPort(Protocol):
    def __call__(self, function_id: UUID) -> FunctionStoragePort: ...


class FunctionExecutionPort(Protocol):
    """Backend execution plane; provider adapters never implement this port."""

    async def execute(
        self,
        run_id: UUID,
        *,
        mode: FunctionDispatchMode,
    ) -> FunctionRunEntity: ...

    async def cancel(self, run_id: UUID) -> FunctionRunEntity: ...


class FunctionSchemaExecutionPort(Protocol):
    async def extract_schemas(
        self,
        *,
        function_id: UUID,
        pod_id: UUID,
        user_id: UUID,
        function_name: str,
        artifact: FunctionArtifact,
    ) -> FunctionSchemaSet:
        raise NotImplementedError


class FunctionRunQueuePort(Protocol):
    """The one backend queue used for asynchronous function runs."""

    async def enqueue(self, run_id: UUID) -> str:
        """Publish the stable ``function_run_id`` and return its queue identity."""
        ...
