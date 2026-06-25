"""Fixtures for the holistic workload-permissions e2e suite.

Shared e2e fixtures (test_app, db_session, fixed_test_user, authenticated_client,
fixed_test_org, db_manager, containers, ...) are inherited from the parent pod
e2e conftest. This module only adds the datastore file-indexing fixture, which
the folder-search tests need and which lives in the datastore module's conftest
(not visible here).
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy import select

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.test_utils import shared_kreuzberg


@pytest_asyncio.fixture(scope="session")
def kreuzberg_url(tmp_path_factory, worker_id):
    """URL of the single Kreuzberg shared across all xdist workers.

    These tests index datastore files in-process and search them, so they need a
    real Kreuzberg. They use the base e2e_settings (not the datastore module's,
    which wires Kreuzberg), so set it up here rather than relying on a datastore
    test happening to run first on the same worker.
    """
    with shared_kreuzberg(tmp_path_factory.getbasetemp().parent, worker_id) as url:
        yield url


@pytest_asyncio.fixture(scope="function")
async def index_datastore_file(db_manager, kreuzberg_url):
    from app.modules.datastore.config import datastore_settings
    from app.modules.datastore.infrastructure.models import DatastoreFile
    from app.modules.datastore.services.file_processing_service import (
        DatastoreFileProcessingService,
    )

    datastore_settings.kreuzberg_url = kreuzberg_url

    async def _index(pod_id, file_id):
        async with db_manager.session_factory() as session:
            result = await session.execute(
                select(DatastoreFile).where(DatastoreFile.id == file_id)
            )
            file_model = result.scalar_one()
            metadata = file_model.file_metadata or {}

        service = DatastoreFileProcessingService(
            pod_id,
            uow_factory=SessionUnitOfWorkFactory(db_manager.session_factory),
        )
        await service.process_file_async(file_id, metadata)

    return _index
