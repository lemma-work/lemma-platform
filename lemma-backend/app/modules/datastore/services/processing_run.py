"""What one processing run is given, and how its failures are classified.

Split from ``file_processing_service`` to keep it under the size ratchet. A run
is driven either by a ``file.process`` work item (fenced completion, safe
points) or directly, in-process; a failure is ``Unavailable`` only with
evidence a dependency is down, and otherwise the document's.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol
from uuid import UUID

import httpx

from app.core.infrastructure.db.transient_errors import is_database_unavailable
from app.core.embeddings.embeddings import EmbeddingProviderError
from app.core.embeddings.local_embedder import EmbeddingModelUnavailableError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.work import (
    Unavailable,
    UnavailableEvidence,
    database_down,
)
from app.modules.datastore.domain.errors import DocumentExtractionUnavailableError
from app.modules.datastore.infrastructure.models import DatastoreFile
from app.modules.datastore.services.search.indexing_availability import (
    EMBEDDING_MODEL_DOWNLOADING,
    embedding_model_is_loading,
)

#: Embedding provider answers that mean "down or busy", not "this document".
_EMBEDDING_UNAVAILABLE_STATUSES = frozenset({502, 503, 504})


class ProcessingRun(Protocol):
    """What one processing run needs from whoever drives it.

    ``checkpoint`` opens a short, committed unit of work for intermediate state;
    ``completion`` the one the outcome is written in (fenced by the work item's
    lease when a worker drives it); ``raise_if_stopping`` is a safe point at
    which a shutting-down worker may hand the item back.
    """

    def checkpoint(self) -> AbstractAsyncContextManager[SqlAlchemyUnitOfWork]: ...

    def completion(self) -> AbstractAsyncContextManager[SqlAlchemyUnitOfWork]: ...

    def raise_if_stopping(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DirectRun:
    """A run with no work item behind it: every unit of work from one factory."""

    uow_factory: UnitOfWorkFactory

    def checkpoint(self) -> AbstractAsyncContextManager[SqlAlchemyUnitOfWork]:
        return self.uow_factory()

    def completion(self) -> AbstractAsyncContextManager[SqlAlchemyUnitOfWork]:
        return self.uow_factory()

    def raise_if_stopping(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    """The row as this run read it. Read-only; the session is gone by the I/O."""

    id: UUID
    pod_id: UUID
    kind: str
    status: str
    path: str
    name: str
    mime_type: str | None
    size_bytes: int
    search_enabled: bool
    content_sha256: str | None
    indexed_sha256: str | None
    storage_layout: str
    owner_user_id: UUID | None
    file_metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def of(cls, model: DatastoreFile) -> "_FileSnapshot":
        return cls(
            id=model.id,
            pod_id=model.pod_id,
            kind=model.kind,
            status=model.status,
            path=model.path,
            name=model.name,
            mime_type=model.mime_type,
            size_bytes=int(model.size_bytes or 0),
            search_enabled=bool(model.search_enabled),
            content_sha256=model.content_sha256,
            indexed_sha256=getattr(model, "indexed_sha256", None),
            storage_layout=getattr(model, "storage_layout", "path") or "path",
            owner_user_id=model.owner_user_id,
            file_metadata=dict(model.file_metadata or {}),
        )


def unavailable_for(exc: BaseException) -> Unavailable | None:
    """The ``Unavailable`` this failure is, if it is evidence a dependency is down.

    Only evidence counts: a refused connection, a failing health check, a
    circuit opened by other documents, a 429, an embedding provider answering
    502/503/504. Anything else is this document's failure.
    """
    # The message is ours, never the exception's: it is stored on the work item,
    # and a provider's error text can carry anything.
    detail = f"{type(exc).__name__}: dependency unavailable"
    if is_database_unavailable(exc):
        # Lemma's own database: the document is not at fault.
        return database_down(exc)
    if isinstance(exc, DocumentExtractionUnavailableError):
        retry_after = exc.retry_after_seconds
        return Unavailable(
            detail,
            evidence=UnavailableEvidence(exc.evidence),
            retry_after=timedelta(seconds=retry_after) if retry_after else None,
        )
    if isinstance(exc, EmbeddingModelUnavailableError):
        return Unavailable(detail, evidence=UnavailableEvidence.HEALTH_CHECK_FAILING)
    if isinstance(exc, EmbeddingProviderError):
        cause = exc.__cause__
        if isinstance(cause, httpx.HTTPStatusError):
            status = cause.response.status_code
            if status == 429:
                return Unavailable(detail, evidence=UnavailableEvidence.RATE_LIMITED)
            if status in _EMBEDDING_UNAVAILABLE_STATUSES:
                return Unavailable(
                    detail, evidence=UnavailableEvidence.HEALTH_CHECK_FAILING
                )
        if isinstance(cause, httpx.TransportError):
            return Unavailable(detail, evidence=UnavailableEvidence.CONNECTION_REFUSED)
    return None


def _waiting_note(exc: BaseException, unavailable: Unavailable) -> str:
    """What the row says while it waits: why, in words a person can act on."""
    if embedding_model_is_loading(exc):
        return EMBEDDING_MODEL_DOWNLOADING
    dependency = (
        "embedding provider"
        if isinstance(exc, (EmbeddingProviderError, EmbeddingModelUnavailableError))
        else "document extractor"
    )
    return (
        f"Waiting for the {dependency} ({unavailable.evidence.value}); "
        "this document will be processed when it is back."
    )
