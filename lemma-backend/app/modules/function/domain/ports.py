"""Function module ports."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.function.domain.entities import (
    FunctionArtifact,
    FunctionDispatchMode,
    FunctionEntity,
    FunctionRevisionEntity,
    FunctionRunEntity,
    FunctionSchemaSet,
)


class FunctionRepositoryPort(Protocol):
    @abstractmethod
    async def get_for_update(self, owner_id: UUID) -> FunctionEntity | None:
        raise NotImplementedError

    @abstractmethod
    async def mark_revisions_purged(self, version_ids: tuple[UUID, ...]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def create(self, entity: FunctionEntity) -> FunctionEntity:
        raise NotImplementedError

    @abstractmethod
    async def get(self, id: UUID) -> FunctionEntity | None:
        raise NotImplementedError

    @abstractmethod
    async def get_by_name(
        self, pod_id: UUID, name: str, ctx: Context | None = None
    ) -> FunctionEntity | None:
        raise NotImplementedError

    @abstractmethod
    async def update(self, function: FunctionEntity) -> FunctionEntity:
        raise NotImplementedError

    @abstractmethod
    async def activate_revision_if_missing(
        self,
        function_id: UUID,
        *,
        expected_code_path: str,
        revision_hash: str,
        code_path: str,
    ) -> FunctionEntity | None:
        raise NotImplementedError

    @abstractmethod
    async def record_revision(
        self, entity: FunctionRevisionEntity
    ) -> FunctionRevisionEntity:
        raise NotImplementedError

    @abstractmethod
    async def get_revision_by_hash(
        self, function_id: UUID, revision_hash: str
    ) -> FunctionRevisionEntity | None:
        raise NotImplementedError

    @abstractmethod
    async def get_revision_by_number(
        self, function_id: UUID, revision_number: int
    ) -> FunctionRevisionEntity | None:
        raise NotImplementedError

    @abstractmethod
    async def list_revisions(self, function_id: UUID) -> list[FunctionRevisionEntity]:
        raise NotImplementedError

    @abstractmethod
    async def revision_hashes_with_runs_in_flight(self, function_id: UUID) -> set[str]:
        raise NotImplementedError

    @abstractmethod
    async def mark_revisions_pruned(self, revision_ids: list[UUID]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def activate_revision(
        self, function_id: UUID, revision: FunctionRevisionEntity
    ) -> FunctionEntity | None:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, id: UUID) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def list_by_pod(
        self, pod_id: UUID, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[FunctionEntity], str | None]:
        raise NotImplementedError

    @abstractmethod
    async def list_visible_by_pod(
        self,
        pod_id: UUID,
        ctx: Context,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[FunctionEntity], str | None]:
        raise NotImplementedError


class FunctionRunRepositoryPort(Protocol):
    async def create_run(self, entity: FunctionRunEntity) -> FunctionRunEntity: ...

    async def get_run(self, run_id: UUID) -> FunctionRunEntity | None: ...

    async def list_runs_by_function(
        self, function_id: UUID, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[FunctionRunEntity], str | None]: ...


class FunctionStorageDeletionPort(Protocol):
    """Storage cleanup operations shared by retention and staged-upload cleanup."""

    @abstractmethod
    async def delete_file(self, path: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete_prefix(self, prefix: str) -> None:
        raise NotImplementedError


class FunctionStoragePort(FunctionStorageDeletionPort, Protocol):
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
