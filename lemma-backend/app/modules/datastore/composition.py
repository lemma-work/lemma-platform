"""Datastore adapter composition for API, worker, and test entrypoints."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from app.core.embeddings.embeddings import Embedder
from app.core.embeddings.factory import create_embedder
from app.core.config import settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.datastore.services.file_processing_service import (
    DatastoreFileProcessingService,
)
from app.modules.datastore.services.search.postgres_search_service import (
    PostgresSearchService,
)


@dataclass(frozen=True, slots=True)
class DatastoreComposition:
    embedder_provider: Callable[[], Embedder]
    preload_embeddings: bool = True

    def build_search_service(self, pod_id: UUID) -> PostgresSearchService:
        return PostgresSearchService(pod_id, embedder=self.embedder_provider())

    def build_processing_service(
        self, pod_id: UUID, *, uow_factory: UnitOfWorkFactory
    ) -> DatastoreFileProcessingService:
        return DatastoreFileProcessingService(
            pod_id,
            uow_factory=uow_factory,
            search_service=self.build_search_service(pod_id),
        )


PRODUCTION_DATASTORE_COMPOSITION = DatastoreComposition(
    embedder_provider=create_embedder,
    preload_embeddings=settings.effective_embedding_provider() == "local",
)
_active_composition = PRODUCTION_DATASTORE_COMPOSITION


def get_datastore_composition() -> DatastoreComposition:
    return _active_composition


def install_datastore_composition(
    composition: DatastoreComposition,
) -> DatastoreComposition:
    """Install an entrypoint-level composition and return the previous one."""
    global _active_composition
    previous = _active_composition
    _active_composition = composition
    return previous
