from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, PrivateAttr

from app.core.authorization.context import ResourceType
from app.core.domain.aggregate import AggregateRoot
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.domain.indexing_policy import is_indexable_mime_type


class FileStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    # No longer written: a failure that may be retried is PENDING with a
    # backoff on its work item. Kept so rows written before that still read.
    FAILED = "FAILED"
    # The one terminal failure: attempts exhausted, a preflight limit, a
    # missing original, or the extractor down past its budget. The reason is in
    # ``last_processing_error``. New bytes or a retry request re-open it;
    # writing the same bytes again does not.
    FAILED_PERMANENT = "FAILED_PERMANENT"


#: Statuses a byte-identical write leaves alone: the file is already processed,
#: being processed, waiting to be, or has failed on exactly these bytes.
_SETTLED_FOR_SAME_BYTES = frozenset(
    {
        FileStatus.PENDING,
        FileStatus.PROCESSING,
        FileStatus.COMPLETED,
        FileStatus.FAILED_PERMANENT,
    }
)


class ProcessingRequest(str, Enum):
    """What asked for a file to be processed; content updates are debounced."""

    CREATED = "created"
    CONTENT = "content"
    RETRY = "retry"
    SEARCH_ENABLED = "search_enabled"


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
    content_sha256: str | None = None
    #: The bytes the current chunks and derived artifacts were made from.
    indexed_sha256: str | None = None
    #: Where the bytes live: ``path`` (keyed by path; a rename copies them) or
    #: ``object`` (keyed by id and sha; a rename touches no bytes).
    storage_layout: str = "path"
    parent_id: UUID | None = None
    allowed_actions: list[str] = Field(default_factory=list)

    #: Why a transition asked for the file to be (re)processed, if one did;
    #: the repository writes the work item in the same transaction as the row.
    _processing_request: ProcessingRequest | None = PrivateAttr(default=None)

    @property
    def processing_request(self) -> "ProcessingRequest | None":
        return self._processing_request

    def clear_processing_request(self) -> None:
        self._processing_request = None

    def _request_processing(self, reason: "ProcessingRequest") -> None:
        self.status = FileStatus.PENDING
        self.indexed_at = None
        self.processing_attempts = 0
        self.last_processing_error = None
        self._processing_request = reason

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
        from app.core.file_types import get_content_type

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
        # Repeating the current setting is not a request to reprocess: an
        # update that merely echoes ``search_enabled`` back must not requeue
        # an indexed file.
        if enabled == self.search_enabled:
            return
        self.search_enabled = enabled
        if not enabled:
            self.status = FileStatus.NOT_REQUIRED
            self.indexed_at = None
            return
        if self.is_file and is_indexable_mime_type(self.mime_type, self.name):
            self._request_processing(ProcessingRequest.SEARCH_ENABLED)
        else:
            self.status = FileStatus.NOT_REQUIRED
            self.indexed_at = None

    def mark_created(self, actor_id: UUID | None = None) -> None:
        from app.modules.datastore.domain.events import DatastoreFileCreatedEvent

        if not self.is_file:
            self.status = FileStatus.NOT_REQUIRED
            return
        if self._is_indexable():
            self._request_processing(ProcessingRequest.CREATED)
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

    def mark_content_updated(
        self,
        actor_id: UUID | None = None,
        *,
        previous_sha256: str | None = None,
        inputs_changed: bool = False,
    ) -> None:
        """New content: process it again -- unless it is the same content.

        ``previous_sha256`` is the digest the row held before this write. Bytes
        identical to it change nothing about the file's processing: a file
        already indexed stays indexed, one in flight stays in flight, and one
        that failed permanently stays failed instead of rejoining the queue
        with a fresh budget, which is how an agent rewriting the same failing
        document kept it looping. ``inputs_changed`` is for writes that change
        what processing reads without changing the bytes (supplied markdown,
        a name that changes how they are read).
        """
        from app.modules.datastore.domain.events import DatastoreFileUpdatedEvent

        if not self.is_file:
            self.status = FileStatus.NOT_REQUIRED
            return
        same_bytes = (
            not inputs_changed
            and previous_sha256 is not None
            and self.content_sha256 == previous_sha256
            and self.status in _SETTLED_FOR_SAME_BYTES
        )
        if same_bytes and self._is_indexable():
            pass
        elif self._is_indexable():
            # New content gets a fresh processing-retry budget.
            self._request_processing(ProcessingRequest.CONTENT)
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

    def mark_retry_requested(self, actor_id: UUID | None = None) -> bool:
        """Offer a failed document to processing again, with a fresh budget.

        Only a failed file: one that is waiting or being read is already on
        its way, and one that finished has nothing to retry. Returns whether
        anything changed, so the caller writes nothing for a no-op. The event
        is the same one a content update sends, because it is what enqueues
        a PENDING row for the worker.
        """
        from app.modules.datastore.domain.events import DatastoreFileUpdatedEvent

        if self.status not in (FileStatus.FAILED, FileStatus.FAILED_PERMANENT):
            return False
        self._request_processing(ProcessingRequest.RETRY)
        self.add_event(
            DatastoreFileUpdatedEvent(
                file_id=self.id,
                pod_id=self.pod_id,
                actor_id=actor_id,
                path=self.path,
                metadata=self.metadata or {},
            )
        )
        return True

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

    def mark_moved(self, actor_id: UUID | None = None) -> None:
        """Announce a path change without claiming the content changed.

        A rename has always emitted ``DatastoreFileUpdatedEvent``, but only as a
        side effect of ``mark_content_updated`` on every descendant -- which
        also set them PENDING, and that is what re-extracted a whole folder's
        worth of documents to produce artifacts identical to the ones it had.

        The event still matters, and for a reason unrelated to indexing: it is
        what drops the cached memory brief, and that cache clears by pod (or by
        owner) prefix, so **one** event covers a renamed subtree -- the hundreds
        that used to follow it were each clearing what the first one had.

        Touching no column is what makes this safe to send: the reindex consumer
        enqueues only a PENDING row, so a file that is merely somewhere else is
        offered to the queue and declined.
        """
        from app.modules.datastore.domain.events import DatastoreFileUpdatedEvent

        self.add_event(
            DatastoreFileUpdatedEvent(
                file_id=self.id,
                pod_id=self.pod_id,
                actor_id=actor_id,
                path=self.path,
                metadata=self.metadata or {},
            )
        )

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
    content: bytes | Path | None = None


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


class DatastoreSignedLinkEntity(BaseModel):
    """A public short link to one file, as the durable record knows it.

    Deliberately not an ``AggregateRoot``: a link has no behaviour and emits no
    events. It is a row that says "this code, until this instant, resolves to
    these bytes" — plus the two things the serving route cannot look up for
    itself, because it runs unauthenticated with no pod context.
    """

    id: UUID
    code: str
    pod_id: UUID
    created_by_user_id: UUID | None = None
    minted_by_workload: str | None = None
    path: str
    object_key: str
    content_type: str
    filename: str
    content_sha256: str | None = None
    size_bytes: int = 0
    max_hits: int
    expires_at: datetime
    revoked_at: datetime | None = None
    exhausted_at: datetime | None = None
    created_at: datetime | None = None

    @property
    def is_live(self) -> bool:
        """Whether this link still resolves.

        Redis owns how much budget is *left*; the row owns the three ways a
        link stops resolving for good. Exhaustion is among them because the
        serving path rehydrates from this row whenever Redis has nothing — and
        spending the budget is precisely when the key gets dropped, so without
        it the very next fetch would mint a fresh budget and serve again.
        """
        if self.revoked_at is not None or self.exhausted_at is not None:
            return False
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc)
