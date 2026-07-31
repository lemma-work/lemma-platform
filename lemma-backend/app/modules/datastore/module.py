"""Datastore module registration."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from app.core.log.log import get_logger
from app.core.request_context import create_background_task
from app.core.registry import LemmaModule

logger = get_logger(__name__)

EmbeddingCapabilityStatus = Literal["disabled", "lazy", "preparing", "ready", "degraded"]


@dataclass(slots=True)
class EmbeddingCapability:
    status: EmbeddingCapabilityStatus = "disabled"
    detail: str = ""


_embedding_capability = EmbeddingCapability()
_embedding_init_task: asyncio.Task[None] | None = None


def embedding_capability() -> EmbeddingCapability:
    """Return a copy of process-local embedding initialization state."""
    return EmbeddingCapability(
        status=_embedding_capability.status,
        detail=_embedding_capability.detail,
    )


async def _initialize_local_embeddings(composition, timeout: float) -> None:
    _embedding_capability.status = "preparing"
    _embedding_capability.detail = "Preparing the local search model"
    logger.debug("datastore.module.preloading_local_embedding_model.observed")
    try:
        async with asyncio.timeout(timeout):
            vector = await composition.embedder_provider().embed(
                "lemma embedding readiness"
            )
        from app.core.config import settings

        if len(vector) != settings.embedding_dimension:
            raise RuntimeError(
                "Local embedding preload returned an unexpected vector dimension"
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - capability degrades without core failure
        _embedding_capability.status = "degraded"
        _embedding_capability.detail = (
            "Local semantic search is unavailable; it will retry when used"
        )
        logger.warning(
            "datastore.module.local_embedding_model_degraded.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return
    _embedding_capability.status = "ready"
    _embedding_capability.detail = "Local semantic search is ready"
    logger.debug("datastore.module.local_embedding_model_ready.observed")


@asynccontextmanager
async def _preload_local_embeddings(context):
    """Initialize local embeddings according to the deployment startup policy."""
    global _embedding_init_task
    del context
    from app.core.config import settings
    from app.modules.datastore.composition import get_datastore_composition

    composition = get_datastore_composition()
    should_preload = settings.local_embedding_preload and composition.preload_embeddings
    mode = settings.local_embedding_startup_mode if should_preload else "lazy"
    if not composition.preload_embeddings:
        _embedding_capability.status = "disabled"
        _embedding_capability.detail = ""
        yield
        return
    if mode == "lazy":
        _embedding_capability.status = "lazy"
        _embedding_capability.detail = "Local semantic search prepares when first used"
        yield
        return

    timeout = max(1.0, settings.local_embedding_preload_timeout_seconds)
    if mode == "blocking":
        # Preserve the existing fail-fast contract outside managed Desktop.
        _embedding_capability.status = "preparing"
        _embedding_capability.detail = "Preparing the local search model"
        logger.debug("datastore.module.preloading_local_embedding_model.observed")
        async with asyncio.timeout(timeout):
            vector = await composition.embedder_provider().embed(
                "lemma embedding readiness"
            )
        if len(vector) != settings.embedding_dimension:
            _embedding_capability.status = "degraded"
            raise RuntimeError(
                "Local embedding preload returned an unexpected vector dimension"
            )
        _embedding_capability.status = "ready"
        _embedding_capability.detail = "Local semantic search is ready"
        logger.debug("datastore.module.local_embedding_model_ready.observed")
        yield
        return

    owner = False
    if _embedding_init_task is None:
        owner = True
        _embedding_init_task = create_background_task(
            _initialize_local_embeddings(composition, timeout),
            name="local-embedding-initializer",
        )
    try:
        yield
    finally:
        if owner and _embedding_init_task is not None:
            if not _embedding_init_task.done():
                _embedding_init_task.cancel()
            try:
                await _embedding_init_task
            except asyncio.CancelledError:
                pass
            finally:
                _embedding_init_task = None


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
