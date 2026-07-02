"""Staging adapter against a local obstore backend (tmp dir)."""

from uuid import uuid4

import pytest
from obstore.store import LocalStore

from app.modules.pod_bundle.infrastructure.staging import (
    BundleStagingStorage,
    archive_key,
)


@pytest.fixture
def staging(tmp_path) -> BundleStagingStorage:
    return BundleStagingStorage(store=LocalStore(prefix=tmp_path, mkdir=True))


async def test_put_get_round_trip(staging: BundleStagingStorage):
    job_id = uuid4()
    key = await staging.put_archive("pod-imports", job_id, b"zip-bytes")
    assert key == archive_key("pod-imports", job_id)

    assert await staging.get_archive("pod-imports", job_id) == b"zip-bytes"


async def test_missing_archive_returns_none(staging: BundleStagingStorage):
    assert await staging.get_archive("pod-imports", uuid4()) is None
    assert await staging.iter_archive("pod-exports", uuid4()) is None


async def test_iter_archive_streams_all_bytes(staging: BundleStagingStorage):
    job_id = uuid4()
    payload = b"x" * 1024
    await staging.put_archive("pod-exports", job_id, payload)

    iterator = await staging.iter_archive("pod-exports", job_id)
    assert iterator is not None
    chunks = [chunk async for chunk in iterator]
    assert b"".join(chunks) == payload


async def test_delete_is_idempotent(staging: BundleStagingStorage):
    job_id = uuid4()
    await staging.put_archive("pod-imports", job_id, b"data")
    await staging.delete_archive("pod-imports", job_id)
    assert await staging.get_archive("pod-imports", job_id) is None
    # Second delete of a missing object is a no-op, not an error.
    await staging.delete_archive("pod-imports", job_id)


async def test_list_archives_by_kind(staging: BundleStagingStorage):
    import_id, export_id = uuid4(), uuid4()
    await staging.put_archive("pod-imports", import_id, b"a")
    await staging.put_archive("pod-exports", export_id, b"b")

    imports = await staging.list_archives("pod-imports")
    exports = await staging.list_archives("pod-exports")

    assert [job_id for job_id, _ in imports] == [import_id]
    assert [job_id for job_id, _ in exports] == [export_id]
