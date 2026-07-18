"""Datastore module registration."""

import asyncio
from contextlib import asynccontextmanager

from app.core.log.log import get_logger
from app.core.registry import LemmaModule

logger = get_logger(__name__)


@asynccontextmanager
async def _preload_local_embeddings(context):
    """Fail API/worker readiness when its local embedding model is unusable."""
    del context
    from app.core.config import settings
    from app.modules.datastore.composition import get_datastore_composition

    composition = get_datastore_composition()
    should_preload = settings.local_embedding_preload and composition.preload_embeddings
    if should_preload:
        timeout = max(1.0, settings.local_embedding_preload_timeout_seconds)
        logger.debug("datastore.module.preloading_local_embedding_model.observed")
        async with asyncio.timeout(timeout):
            vector = await composition.embedder_provider().embed(
                "lemma embedding readiness"
            )
        if len(vector) != settings.embedding_dimension:
            raise RuntimeError(
                "Local embedding preload returned an unexpected vector dimension"
            )
        logger.debug("datastore.module.local_embedding_model_ready.observed")
    yield


def _routers():
    from app.modules.datastore.api.controllers.record_controller import router as record
    from app.modules.datastore.api.controllers.query_controller import router as query
    from app.modules.datastore.api.controllers.table_controller import router as table
    from app.modules.datastore.api.controllers.file_controller import router as file
    from app.modules.datastore.api.controllers.public_file_controller import (
        router as public_file,
    )
    from app.modules.datastore.api.controllers.signed_file_controller import (
        router as signed_file,
    )
    from app.modules.datastore.api.controllers.changes_controller import (
        router as changes,
    )

    return [record, query, table, file, public_file, signed_file, changes]


def _event_routers():
    from app.modules.datastore.events.handlers import router
    from app.modules.datastore.events.pod_schema_consumer import (
        router as pod_schema_router,
    )

    return [router, pod_schema_router]


@asynccontextmanager
async def _backfill_query_role(app):
    """Ensure the RLS-subject role can read every existing pod schema, so ad-hoc
    datastore queries (run under that role) are scoped. Non-fatal: new tables
    also grant on creation, and queries fail closed."""
    from app.modules.datastore.infrastructure.transactional_events import (
        ensure_datastore_event_outbox,
    )

    # Fail startup when the durable event table cannot be established. Record
    # mutation must never degrade to post-commit best-effort publication.
    await ensure_datastore_event_outbox()

    try:
        from app.modules.datastore.api.dependencies import get_schema_manager

        await get_schema_manager().backfill_query_role_grants()
        logger.debug("datastore.module.datastore_query_role_grants_ensured.observed")
    except Exception:  # noqa: BLE001
        logger.debug(
            'datastore.module.ensure_datastore_query_role_grants.diagnostic',
            exc_info=True,
        )
    yield


@asynccontextmanager
async def _datastore_outbox_dispatcher(context):
    """Dispatch the second outbox when pod schemas use a separate database."""
    from app.core.config import settings
    from app.core.infrastructure.events.message_bus import get_message_bus
    from app.core.infrastructure.events.outbox import outbox_dispatcher_lifespan
    from app.modules.datastore.infrastructure.session import (
        get_datastore_session_maker,
    )
    from app.modules.datastore.infrastructure.transactional_events import (
        ensure_datastore_event_outbox,
    )

    del context
    datastore_url = settings.datastore_database_url or settings.database_url
    if datastore_url == settings.database_url:
        yield
        return
    await ensure_datastore_event_outbox()
    async with outbox_dispatcher_lifespan(
        get_datastore_session_maker(), get_message_bus()
    ):
        yield


@asynccontextmanager
async def _close_reindex_queue(context):
    try:
        yield
    finally:
        from app.modules.datastore.infrastructure.reindex_queue import (
            close_datastore_reindex_queue,
        )

        await close_datastore_reindex_queue()


module = LemmaModule(
    name="datastore",
    routers=_routers,
    event_routers=_event_routers,
    api_lifespans=(_preload_local_embeddings, _backfill_query_role),
    worker_lifespans=(
        _preload_local_embeddings,
        _datastore_outbox_dispatcher,
        _close_reindex_queue,
    ),
    stream_groups=(
        ("datastore.events", "datastore-file-events"),
        ("pod_events", "pod-provisioning-events"),
    ),
)
