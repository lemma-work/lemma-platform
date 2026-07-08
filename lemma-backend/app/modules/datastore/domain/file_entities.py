from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.authorization.context import ResourceType
from app.core.domain.aggregate import AggregateRoot
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.domain.indexing_policy import is_indexable_mime_type


class FileStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    # Terminal failure: the file exhausted its processing-retry budget (or is
    # unprocessable, e.g. too large). Unlike FAILED, the recovery cron never
    # re-drives a FAILED_PERMANENT file, so it cannot rejoin the poison queue.
    # A fresh upload / content update re-opens it (status -> PENDING, attempts
    # reset) via mark_content_updated / set_search_enabled below.
    FAILED_PERMANENT = "FAILED_PERMANENT"


class FileKind(str, Enum):
    FILE = "FILE"
    FOLDER = "FOLDER"


class SearchMethod(str, Enum):
    VECTOR = "VECTOR"
    TEXT = "TEXT"
    HYBRID = "HYBRID"


class DatastoreFileEntity(AggregateRoot):
    pod_id: UUID
    owner_user_id: UUID | None = None
    kind: FileKind = FileKind.FILE
    visibility: str = "PERSONAL"
    path: str
    name: str
    description: str | None = None
    mime_type: str | None = None
    size_bytes: int = 0
    search_enabled: bool = True
    status: FileStatus = FileStatus.PENDING
    metadata: dict[str, Any] | None = None
    indexed_at: datetime | None = None
    last_processing_error: str | None = None
    processing_attempts: int = 0
    allowed_actions: list[str] = Field(default_factory=list)

    @property
    def is_folder(self) -> bool:
        return self.kind == FileKind.FOLDER

    @property
    def is_file(self) -> bool:
        return self.kind == FileKind.FILE

    @property
    def content_type(self) -> str:
        if self.mime_type:
            return self.mime_type
        if self.is_folder:
            return "application/x-directory"
        from app.modules.agent.domain.file_entities import get_content_type

        return get_content_type(self.name)

    @property
    def resource_type(self) -> ResourceType:
        if self.is_folder:
            return ResourceType.FOLDER
        return ResourceType.DOCUMENT

    def rename(self, name: str) -> None:
        if not name or not name.strip():
            raise DatastoreValidationError("File name cannot be empty")
        self.name = name.strip()

    def update_description(self, description: str | None) -> None:
        self.description = description

    def update_metadata(self, metadata: dict[str, Any] | None) -> None:
        self.metadata = metadata

    def _is_indexable(self) -> bool:
        """A file is indexed only when search is enabled AND its type is an
        indexable document format (see ``indexing_policy``)."""
        return self.search_enabled and is_indexable_mime_type(self.mime_type, self.name)

    def set_search_enabled(self, enabled: bool) -> None:
        self.search_enabled = enabled
        if not enabled:
            self.status = FileStatus.NOT_REQUIRED
            self.indexed_at = None
            return
        if self.is_file and is_indexable_mime_type(self.mime_type, self.name):
            self.status = FileStatus.PENDING
            self.indexed_at = None
            self.processing_attempts = 0
        else:
            self.status = FileStatus.NOT_REQUIRED
            self.indexed_at = None

    def mark_created(self, actor_id: UUID | None = None) -> None:
        from app.modules.datastore.domain.events import DatastoreFileCreatedEvent

        if not self.is_file:
            self.status = FileStatus.NOT_REQUIRED
            return
        if self._is_indexable():
            self.status = FileStatus.PENDING
            self.indexed_at = None
        else:
            self.status = FileStatus.NOT_REQUIRED
            self.indexed_at = None
        self.add_event(
            DatastoreFileCreatedEvent(
                file_id=self.id,
                pod_id=self.pod_id,
                actor_id=actor_id,
                path=self.path,
                metadata=self.metadata or {},
            )
        )

    def mark_content_updated(self, actor_id: UUID | None = None) -> None:
        from app.modules.datastore.domain.events import DatastoreFileUpdatedEvent

        if not self.is_file:
            self.status = FileStatus.NOT_REQUIRED
            return
        if self._is_indexable():
            self.status = FileStatus.PENDING
            self.indexed_at = None
            # New content gets a fresh processing-retry budget.
            self.processing_attempts = 0
        else:
            self.status = FileStatus.NOT_REQUIRED
            self.indexed_at = None
        self.add_event(
            DatastoreFileUpdatedEvent(
                file_id=self.id,
                pod_id=self.pod_id,
                actor_id=actor_id,
                path=self.path,
                metadata=self.metadata or {},
            )
        )

    def mark_processing(self) -> None:
        self.status = FileStatus.PROCESSING

    def mark_completed(self) -> None:
        self.status = FileStatus.COMPLETED
        self.indexed_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str | None = None) -> None:
        self.status = FileStatus.FAILED
        self.last_processing_error = error

    def mark_failed_permanent(self, error: str | None = None) -> None:
        """Terminal failure the recovery cron never re-drives (see FileStatus)."""
        self.status = FileStatus.FAILED_PERMANENT
        self.last_processing_error = error

    def mark_deleted(self, actor_id: UUID | None = None) -> None:
        from app.modules.datastore.domain.events import DatastoreFileDeletedEvent

        self.add_event(
            DatastoreFileDeletedEvent(
                file_id=self.id,
                pod_id=self.pod_id,
                actor_id=actor_id,
                path=self.path,
                metadata=self.metadata or {},
            )
        )


class DatastoreFileUpdateEntity(BaseModel):
    path: str | None = None
    new_path: str | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None
    visibility: str | None = None
    search_enabled: bool | None = None
    content: bytes | None = None


class DatastoreFileSearchResult(BaseModel):
    file_id: UUID
    path: str
    chunk_index: int
    content: str
    metadata: dict[str, Any] = {}
    score: float | None = None
    # 1-based page the chunk came from (and page_end when it spans pages).
    # None for files without page markers (plaintext/markdown) or indexed before
    # page annotation was added.
    page_number: int | None = None
    page_end: int | None = None
