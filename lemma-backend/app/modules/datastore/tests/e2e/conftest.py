"""Datastore module E2E fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
import subprocess
from typing import Literal

import pytest
import pytest_asyncio
from fastapi import status
from httpx import AsyncClient
from sqlalchemy import select

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.config import settings
from app.core.test_utils import shared_kreuzberg
from app.modules.datastore.tests.e2e.harness import (
    DatastoreApi,
    invite_to_pod,
    pod_payload,
    signup_user,
)
from app.modules.datastore.tests.e2e.fake_document_processors import (
    FakeDocumentProcessorServer,
)
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.composition import (
    DatastoreComposition,
    get_datastore_composition,
    install_datastore_composition,
)
from app.modules.test_support.embeddings import DeterministicTestEmbedder
from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.worker_process import production_worker_process

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="session", autouse=True)
def hermetic_datastore_runtime():
    """Avoid model-network dependencies in routine datastore E2E tests."""
    previous_layout = datastore_settings.document_processing_layout_enabled
    embedder = DeterministicTestEmbedder(settings.embedding_dimension)
    previous_composition = install_datastore_composition(
        DatastoreComposition(embedder_provider=lambda: embedder)
    )
    datastore_settings.document_processing_layout_enabled = False
    try:
        yield
    finally:
        install_datastore_composition(previous_composition)
        datastore_settings.document_processing_layout_enabled = previous_layout


# Use the base session settings unchanged. Kreuzberg is NOT wired in here: it is
# a RAM-heavy ML container, so only the fixtures that actually extract documents
# (``index_datastore_file`` below, and ``kreuzberg_url`` consumers like
# ``test_kreuzberg_helper_e2e``) pull it in — the rest of the datastore e2e suite
# runs on just postgres/redis/supertokens and never starts it.
e2e_settings = e2e_fixtures.e2e_settings

test_network = e2e_fixtures.test_network
postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
worker = e2e_fixtures.worker
db_manager = e2e_fixtures.db_manager
test_app = e2e_fixtures.test_app
db_session = e2e_fixtures.db_session
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org
scenario = e2e_fixtures.scenario

DocumentProcessorName = Literal["kreuzberg", "docling", "markitdown"]


@pytest_asyncio.fixture(scope="session")
async def fake_document_processor_server() -> AsyncIterator[
    FakeDocumentProcessorServer
]:
    server = FakeDocumentProcessorServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
def document_worker(
    e2e_settings,
    fake_document_processor_server: FakeDocumentProcessorServer,
) -> Callable[
    [DocumentProcessorName], AbstractAsyncContextManager[subprocess.Popen[str]]
]:
    """Start a production worker against one deterministic processor adapter."""

    @asynccontextmanager
    async def _start(processor: DocumentProcessorName):
        extra_env = {
            "E2E_DISABLE_WORKER_FILE_AUTOINDEX": "false",
            "DOCUMENT_PROCESSOR": processor,
            "KREUZBERG_URL": fake_document_processor_server.base_url,
            "KREUZBERG_REQUEST_TIMEOUT_SECONDS": "1",
            "KREUZBERG_CONNECT_TIMEOUT_SECONDS": "0.2",
            "KREUZBERG_TRANSIENT_RETRY_ATTEMPTS": "2",
            "KREUZBERG_TRANSIENT_RETRY_BASE_DELAY_SECONDS": "0.01",
            "KREUZBERG_CIRCUIT_FAILURE_THRESHOLD": "20",
            "DOCLING_SERVE_URL": fake_document_processor_server.base_url,
            "DOCLING_REQUEST_TIMEOUT_SECONDS": "2",
            "DOCUMENT_PROCESSING_DEBOUNCE_SECONDS": "0",
        }
        if processor == "markitdown":
            fake_dependencies = Path(__file__).parent / "fake_processor_deps"
            extra_env["PYTHONPATH"] = f"{fake_dependencies}:."
        async with production_worker_process(
            e2e_settings,
            log_prefix=f"lemma_datastore_{processor}_worker",
            extra_env=extra_env,
            worker_entrypoint=(
                "app.modules.datastore.tests.e2e.worker_entrypoint:streaq_worker"
            ),
        ) as process:
            yield process

    return _start


@pytest.fixture(scope="session")
def kreuzberg_url(tmp_path_factory, worker_id):
    """URL of the single Kreuzberg shared across all xdist workers + the worker.

    Shared so the heavy embedding container runs once (see
    ``app.core.test_utils.shared_kreuzberg``). The streaq worker uses the same
    container via the ``worker`` fixture, which is why this lives in shared
    test_utils rather than inline here.
    """
    with shared_kreuzberg(tmp_path_factory.getbasetemp().parent, worker_id) as url:
        yield url


@pytest.fixture
def kreuzberg_wired(kreuzberg_url):
    """Point in-process document processing at the shared Kreuzberg.

    Depend on this (rather than wiring Kreuzberg into the session-wide
    ``e2e_settings``) from any fixture/test that actually extracts a document —
    that's what keeps the RAM-heavy container from starting for the datastore
    tests that never touch one.
    """
    datastore_settings.kreuzberg_url = kreuzberg_url
    return kreuzberg_url


@pytest.fixture
async def pod_api(authenticated_client: AsyncClient, fixed_test_org) -> DatastoreApi:
    response = await authenticated_client.post(
        "/pods", json=pod_payload(fixed_test_org["id"])
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return DatastoreApi(authenticated_client, response.json()["id"])


@pytest.fixture
async def member_users(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    pod_api: DatastoreApi,
) -> dict[str, dict[str, str]]:
    viewer = await signup_user(async_client, "datastore-viewer")
    editor = await signup_user(async_client, "datastore-editor")
    await invite_to_pod(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_api.pod_id,
        user=viewer,
        role="POD_VIEWER",
    )
    await invite_to_pod(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_api.pod_id,
        user=editor,
        role="POD_EDITOR",
    )
    return {"viewer": viewer, "editor": editor}


@pytest_asyncio.fixture(scope="function")
async def index_datastore_file(db_manager, kreuzberg_wired):
    import asyncio

    from app.modules.datastore.domain.file_entities import FileStatus
    from app.modules.datastore.infrastructure.models import DatastoreFile

    _TERMINAL = {FileStatus.COMPLETED.value, FileStatus.NOT_REQUIRED.value}

    async def _file_status(file_id):
        async with db_manager.session_factory() as session:
            result = await session.execute(
                select(DatastoreFile).where(DatastoreFile.id == file_id)
            )
            file_model = result.scalar_one()
            return file_model.status, (file_model.file_metadata or {})

    async def _index(pod_id, file_id):
        _, metadata = await _file_status(file_id)

        service = get_datastore_composition().build_processing_service(
            pod_id,
            uow_factory=SessionUnitOfWorkFactory(db_manager.session_factory),
        )
        # If the upload already enqueued worker indexing, the file may not be
        # PENDING and process_file_async returns immediately (skipped) — the
        # worker is still indexing async. Either way, wait until indexing has
        # actually finished so the subsequent search sees a populated index;
        # otherwise the search races the indexer and returns nothing under load.
        await service.process_file_async(file_id, metadata)
        for _ in range(120):  # ~60s at 0.5s
            status, _ = await _file_status(file_id)
            if status in _TERMINAL:
                return
            if status == FileStatus.FAILED.value:
                raise AssertionError(f"Indexing failed for file {file_id}")
            await asyncio.sleep(0.5)
        raise AssertionError(
            f"Indexing did not complete for file {file_id} (last status: {status})"
        )

    return _index


__all__ = [
    "async_client",
    "authenticated_client",
    "db_manager",
    "db_session",
    "document_worker",
    "e2e_settings",
    "fixed_test_org",
    "fixed_test_user",
    "fake_document_processor_server",
    "index_datastore_file",
    "kreuzberg_url",
    "kreuzberg_wired",
    "member_users",
    "pod_api",
    "postgres_container",
    "redis_container",
    "scenario",
    "supertokens_container",
    "test_app",
    "test_database_url",
    "test_network",
    "test_redis_url",
    "worker",
]
