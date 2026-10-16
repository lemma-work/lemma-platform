"""A worker for one test to kill: claims ingestion items and runs them.

Run as ``python -m app.modules.datastore.tests.e2e.file_work_runner <lease>``
with the database, Redis and Kreuzberg in the environment. It registers the
file kinds with the test budgets and a short lease, and loops claiming and
executing until it is killed -- which is the point: a SIGKILL mid-extraction
leaves a running item that only lease expiry can recover.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta

from app.core.config import settings
from app.modules.datastore.composition import (
    DatastoreComposition,
    install_datastore_composition,
)
from app.modules.test_support.embeddings import DeterministicTestEmbedder

_embedder = DeterministicTestEmbedder(settings.embedding_dimension)
install_datastore_composition(DatastoreComposition(embedder_provider=lambda: _embedder))


async def _run(lease_seconds: float) -> None:
    from app.worker import register_worker_modules  # noqa: E402

    register_worker_modules()  # every module's kinds, as the worker registers them
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.work import WorkClass
    from app.core.infrastructure.work.testing import dispatcher_for
    from app.modules.datastore.tests.e2e.file_work_support import file_work_registry

    registry = file_work_registry(lease=timedelta(seconds=lease_seconds))
    dispatcher = dispatcher_for(
        async_session_maker, WorkClass.INGESTION, registry=registry, owner="runner"
    )
    # The production loop: claim, start, idle on the poll until killed.
    await dispatcher.run()


if __name__ == "__main__":
    asyncio.run(_run(float(sys.argv[1])))
