"""Hermetic datastore worker composition used only by subprocess E2E tests."""

from app.core.config import settings
from app.modules.datastore.composition import (
    DatastoreComposition,
    install_datastore_composition,
)
from app.modules.test_support.embeddings import DeterministicTestEmbedder

_embedder = DeterministicTestEmbedder(settings.embedding_dimension)
install_datastore_composition(DatastoreComposition(embedder_provider=lambda: _embedder))

# Import only after installing the test composition; worker lifespans and tasks
# resolve it when the subprocess starts.
from app.events import streaq_worker  # noqa: E402

__all__ = ["streaq_worker"]
