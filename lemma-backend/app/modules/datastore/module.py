"""Datastore module registration."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from app.modules.datastore.config import datastore_settings
from app.core.log.log import get_logger
from app.core.request_context import create_background_task
from app.core.registry import LemmaModule

logger = get_logger(__name__)

EmbeddingCapabilityStatus = Literal[
    "disabled", "lazy", "preparing", "ready", "degraded"
]


@dataclass(slots=True)
class EmbeddingCapability:
    status: EmbeddingCapabilityStatus = "disabled"
    detail: str = ""


_embedding_capability = EmbeddingCapability()
_embedding_init_task: asyncio.Task[None] | None = None


_PRELOAD_RETRY_INITIAL_SECONDS = 30.0
_PRELOAD_RETRY_MAX_SECONDS = 600.0


def embedding_capability() -> EmbeddingCapability:
    """Return a copy of process-local embedding initialization state.

    The startup preload is one source; the embedder itself is the other. A
    model the preload could not download but a later search or document did
    is ready, and one being fetched right now is preparing, whatever the
    preload last recorded -- so the live state wins once the model was asked
    for at all.
    """
    status, detail = _embedding_capability.status, _embedding_capability.detail
    if status != "disabled":
        live = _live_model_status()
        if live == "ready":
            status, detail = "ready", "Local semantic search is ready"
        elif live == "loading":
            status, detail = "preparing", "Downloading the local search model"
        elif live == "failed":
            status, detail = "degraded", _DEGRADED_DETAIL
    return EmbeddingCapability(status=status, detail=detail)


_DEGRADED_DETAIL = "Could not download the local search model; it retries automatically"


def _live_model_status() -> str | None:
    from app.modules.datastore.composition import get_datastore_composition

    readiness = getattr(
        get_datastore_composition().embedder_provider(), "readiness", None
    )
    if readiness is None:
        return None
    live = readiness().status
    return None if live == "idle" else live


async def _initialize_local_embeddings(composition, timeout: float) -> None:
    """Prepare the model in the background, retrying until it succeeds.

    The first run downloads the model, and a Mac that is offline at that
    moment must not be left without search until the next restart: the
    attempt repeats with a growing pause, so it recovers on its own once the
    machine is online again.
    """
    delay = _PRELOAD_RETRY_INITIAL_SECONDS
    while not await _try_local_embeddings(composition, timeout):
        await asyncio.sleep(delay)
        delay = min(delay * 2, _PRELOAD_RETRY_MAX_SECONDS)


async def _try_local_embeddings(composition, timeout: float) -> bool:
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
        _embedding_capability.detail = _DEGRADED_DETAIL
        logger.warning(
            "datastore.module.local_embedding_model_degraded.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return False
    _embedding_capability.status = "ready"
    _embedding_capability.detail = "Local semantic search is ready"
    logger.debug("datastore.module.local_embedding_model_ready.observed")
    return True


@asynccontextmanager
async def _preload_local_embeddings(context):
    """Initialize local embeddings according to the deployment startup policy."""
    global _embedding_init_task
    del context
    from app.core.config import settings
    from app.modules.datastore.composition import get_datastore_composition

    composition = get_datastore_composition()
    should_preload = (
        datastore_settings.local_embedding_preload and composition.preload_embeddings
    )
    mode = datastore_settings.local_embedding_startup_mode if should_preload else "lazy"
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

    timeout = max(1.0, datastore_settings.local_embedding_preload_timeout_seconds)
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
    from app.modules.datastore.api.controllers.public_rows_controller import (
        router as public_rows,
    )
    from app.modules.datastore.api.controllers.file_controller import router as file
    from app.modules.datastore.api.controllers.signed_link_controller import (
        router as signed_link,
    )
    from app.modules.datastore.api.controllers.public_file_controller import (
        router as public_file,
    )
    from app.modules.datastore.api.controllers.signed_file_controller import (
        router as signed_file,
    )
    from app.modules.datastore.api.controllers.changes_controller import (
        router as changes,
    )
    from app.modules.datastore.api.controllers.processing_controller import (
        router as processing,
    )

    # `signed_link` before `file`, and the order is load-bearing: routes match
    # in registration order, and `file` owns `/files/{file_id}` — which happily
    # matches `/files/signed-urls` and then fails parsing it as a UUID.
    return [
        record,
        query,
        table,
        public_rows,
        signed_link,
        processing,
        file,
        public_file,
        signed_file,
        changes,
    ]


def _live():
    from app.core.live.topics import LiveContribution
    from app.modules.datastore.services.live.topic import RecordsTopic

    return LiveContribution(topics=(RecordsTopic(),))


def _register_work() -> None:
    # In every process: the pod-create transaction (API) writes this kind
    # through the `PodCreated` fact this module subscribes it to.
    import app.modules.datastore.events.contact_merged_work  # noqa: F401
    import app.modules.datastore.events.pod_provision_work  # noqa: F401

    # Every process: a schedule or MCP subscription change (API) writes
    # `changes.sync_subscribers`; the worker claims every change-log kind.
    import app.modules.datastore.changes.horizon  # noqa: F401
    import app.modules.datastore.changes.retention  # noqa: F401
    import app.modules.datastore.changes.work  # noqa: F401


def _register_periodic_work() -> None:
    import app.modules.datastore.events.handlers  # noqa: F401
    import app.modules.datastore.events.orphan_schema_tasks  # noqa: F401

    # Work-runtime kinds, registered here so the worker's dispatchers can
    # claim them; the API registers ``file_work`` by importing the repository
    # that enqueues it.
    import app.modules.datastore.events.file_work  # noqa: F401
    import app.modules.datastore.events.pod_schema_work  # noqa: F401
    import app.modules.datastore.events.storage_work  # noqa: F401


@asynccontextmanager
async def _change_handoff(context):
    """Hand watched writes' wakes to ``changes.deliver`` items.

    Replaces the datastore outbox dispatcher: record changes no longer travel
    as stream events. One loop per worker; it holds one direct connection to
    the datastore database for ``LISTEN`` and polls as a fallback.
    """
    from app.core.config import settings
    from app.core.infrastructure.db.session import async_session_maker
    from app.modules.datastore.changes.handoff import change_handoff_lifespan
    from app.modules.datastore.infrastructure.session import get_datastore_engine

    del context
    datastore_url = datastore_settings.datastore_database_url or settings.database_url
    async with change_handoff_lifespan(
        get_datastore_engine(), async_session_maker, database_url=datastore_url
    ):
        yield


@asynccontextmanager
async def _close_datastore_engine(context):
    """Dispose this module's own engine when the worker stops.

    It used to be a function-local import and a call at the end of
    `streaq_runtime`'s shutdown, which is one of the two things core did that
    made it import a module. The module owns the engine, so the module closes
    it.

    Registered *first* on purpose. `AsyncExitStack` unwinds last-entered-first,
    so entering this one first makes it the last of datastore's to unwind --
    after the change handoff, which can still want the engine on its way out.
    """
    try:
        yield
    finally:
        from app.modules.datastore.infrastructure.session import (
            close_datastore_engine,
        )

        await close_datastore_engine()


def _resource_names():
    """How this module's resources are addressed by name in a grant.

    A thunk so the ORM import happens at assembly rather than whenever the
    module registry is imported. `app/core/authorization/resource_names.py`
    used to hold this table for every module at once.
    """
    from app.core.authorization.context import ResourceType
    from app.core.authorization.resource_names import ResourceNameTable
    from app.modules.datastore.infrastructure.models.datastore_models import (
        DatastoreFile,
        DatastoreTable,
    )

    return (
        (
            ResourceType.DATASTORE_TABLE,
            ResourceNameTable(
                DatastoreTable.id, DatastoreTable.pod_id, DatastoreTable.table_name
            ),
        ),
        # `FOLDER` and `DOCUMENT` are the same rows, addressed by path.
        (
            ResourceType.FOLDER,
            ResourceNameTable(
                DatastoreFile.id, DatastoreFile.pod_id, DatastoreFile.path
            ),
        ),
        (
            ResourceType.DOCUMENT,
            ResourceNameTable(
                DatastoreFile.id, DatastoreFile.pod_id, DatastoreFile.path
            ),
        ),
    )


module = LemmaModule(
    name="datastore",
    resource_names=_resource_names,
    routers=_routers,
    live=_live,
    register_work=_register_work,
    register_periodic_work=_register_periodic_work,
    # Nothing here may scale with data: see `test_boot_hooks.py`. The query
    # role and its grants are ensured lazily; the change handoff starts a loop
    # and reads nothing at boot.
    api_lifespans=(_preload_local_embeddings,),
    worker_lifespans=(
        _close_datastore_engine,
        _preload_local_embeddings,
        _change_handoff,
    ),
)
