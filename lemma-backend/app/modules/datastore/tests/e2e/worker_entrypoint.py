"""Hermetic datastore worker composition used only by subprocess E2E tests.

Run as a module (``python -m ...worker_entrypoint``) so it starts *every* lane,
exactly like ``app.worker`` does in production. Exposing a single streaq Worker
object for the ``streaq run`` CLI would consume only the interactive queue and
silently never process documents, which now live on the bulk lane.
"""

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
from app.core.infrastructure.jobs.streaq_runtime import (  # noqa: E402
    run_worker_lanes,
)

__all__ = ["streaq_worker", "run_worker_lanes"]


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_worker_lanes())
