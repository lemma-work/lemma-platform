from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import update

from app.modules.datastore.infrastructure.models import DatastoreFile
from app.modules.datastore.infrastructure.repositories.file_repository import (
    DatastoreFileRepository,
)
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_hash_and_attempt_fence_rejects_stale_worker(
    pod_api: DatastoreApi, db_session
):
    uploaded = await pod_api.upload_file("fenced.md", b"generation A")
    file_id = UUID(uploaded["id"])
    hash_a = uploaded["content_sha256"]
    hash_b = "b" * 64
    repository = DatastoreFileRepository(SimpleNamespace(session=db_session))

    attempt_a = await repository.claim_for_processing(file_id, content_sha256=hash_a)
    assert attempt_a == 1
    await db_session.commit()

    await db_session.execute(
        update(DatastoreFile)
        .where(DatastoreFile.id == file_id)
        .values(
            content_sha256=hash_b,
            status="PENDING",
            processing_attempts=0,
        )
    )
    await db_session.commit()
    attempt_b = await repository.claim_for_processing(file_id, content_sha256=hash_b)
    assert attempt_b == 1

    assert not await repository.is_processing_claim_current(
        file_id,
        content_sha256=hash_a,
        processing_attempt=attempt_a,
    )
    assert not await repository.mark_completed(
        file_id,
        content_sha256=hash_a,
        processing_attempt=attempt_a,
        file_metadata={"generation": "A"},
    )
    assert not await repository.mark_failed(
        file_id,
        content_sha256=hash_a,
        processing_attempt=attempt_a,
        error="stale failure",
    )
    assert await repository.is_processing_claim_current(
        file_id,
        content_sha256=hash_b,
        processing_attempt=attempt_b,
    )
