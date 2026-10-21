from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select, update

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
    # The person's count started again with the new bytes; the token did not,
    # so it can never be handed out a second time.
    assert attempt_b == attempt_a + 1

    assert not await repository.is_processing_claim_current(
        file_id,
        content_sha256=hash_a,
        claim=attempt_a,
    )
    assert not await repository.mark_completed(
        file_id,
        content_sha256=hash_a,
        claim=attempt_a,
        file_metadata={"generation": "A"},
    )
    assert not await repository.release_to_pending(
        file_id,
        content_sha256=hash_a,
        claim=attempt_a,
        note="stale failure",
        charged=True,
    )
    assert not await repository.mark_failed_permanent_for(
        file_id,
        content_sha256=hash_a,
        claim=attempt_a,
        error="stale failure",
    )
    assert await repository.is_processing_claim_current(
        file_id,
        content_sha256=hash_b,
        claim=attempt_b,
    )

    # Asked again for the same bytes (a retry): the count is cleared, which is
    # what used to hand the stalled run's token straight back to it.
    await db_session.execute(
        update(DatastoreFile)
        .where(DatastoreFile.id == file_id)
        .values(status="PENDING", processing_attempts=0)
    )
    await db_session.commit()
    attempt_c = await repository.claim_for_processing(file_id, content_sha256=hash_b)
    assert attempt_c not in {attempt_a, attempt_b}
    assert not await repository.mark_completed(
        file_id,
        content_sha256=hash_b,
        claim=attempt_b,
        file_metadata={"generation": "B, stalled"},
    )


@pytest.mark.asyncio
async def test_processing_attempts_count_failures_on_these_bytes(
    pod_api: DatastoreApi, db_session
):
    """The person's number, as it read before the work runtime: an outage gives
    the attempt back, a failure on the document keeps it, completing clears it."""
    uploaded = await pod_api.upload_file("counted.md", b"counted")
    file_id = UUID(uploaded["id"])
    sha = uploaded["content_sha256"]
    repository = DatastoreFileRepository(SimpleNamespace(session=db_session))

    async def attempts() -> int:
        return await db_session.scalar(
            select(DatastoreFile.processing_attempts).where(DatastoreFile.id == file_id)
        )

    base = await attempts()
    claim = await repository.claim_for_processing(file_id, content_sha256=sha)
    assert await repository.release_to_pending(
        file_id, content_sha256=sha, claim=claim, note="extractor down", charged=False
    )
    assert await attempts() == base

    claim = await repository.claim_for_processing(file_id, content_sha256=sha)
    assert await repository.release_to_pending(
        file_id, content_sha256=sha, claim=claim, note="bad pdf", charged=True
    )
    assert await attempts() == base + 1

    claim = await repository.claim_for_processing(file_id, content_sha256=sha)
    assert await repository.mark_completed(
        file_id, content_sha256=sha, claim=claim, file_metadata={}
    )
    assert await attempts() == 0
    await db_session.commit()
